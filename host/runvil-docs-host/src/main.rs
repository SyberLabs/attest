//! runvil-docs-host: one prompt run per process for Attest (docs/CLI-RUNNER-SPEC.md).
//!
//! One JSON object in on stdin:
//!
//! ```json
//! {"prompt": "<prompt file text>", "args": [], "script": ["reply 1", "reply 2"],
//!  "seed": 123, "started_at": "2026-01-01T00:00:00Z"}
//! ```
//!
//! and one `PromptObservation` out on stdout:
//!
//! ```json
//! {"status": "completed", "result": "hello", "error_kind": null, "error_line": null,
//!  "message": null, "rounds": 0, "host_version": "runvil@c27899e7..."}
//! ```
//!
//! The Host reads the prompt with the Engine's parser first, so a parse
//! failure keeps its kind and line, then runs it through the Harness with no
//! Packs installed, a fresh memory store, a tokio timer, and a broker that
//! plays model rounds from `script` in order. A round the script does not
//! cover ends the run as `needs_script`, never as a guess. A prompt that
//! declares a Pack this Host does not have ends as `refused` with the
//! Engine's own notice.
//!
//! `seed` and `started_at` are accepted but not applied: the Harness draws the
//! run's seed from the OS and its start from the wall clock itself, and a Host
//! runs prompts only through the Harness (Runvil's AGENTS.md).

use std::collections::VecDeque;
use std::io::{Read, Write};
use std::num::NonZeroU32;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use harness::pack::{HostContext, HostServices, PackRegistry};
use harness::record::{MemoryRecorder, RunOutcome};
use harness::vfs::VfsRef;
use harness::{BoxFuture, Harness, HostSnapshot, InferenceBroker, RunRequest, Timer};
use runvil::Prompt;
use runvil::effect::Round;
use runvil::model::{
    Completion, CompletionError, CompletionErrorKind, CompletionOptions, CompletionResult, Message,
    ModelBinding, ModelCatalog, ModelId, ModelInfo, ThinkingMode, ToolSchema,
};
use serde::{Deserialize, Serialize};

/// The one model the scripted broker lists, and the model the run selects.
const MODEL: &str = "script";

/// The run's name: every event's `execution`.
const EXECUTION: &str = "attest";

#[derive(Deserialize)]
struct Request {
    prompt: String,
    #[serde(default)]
    args: Vec<String>,
    #[serde(default)]
    script: Vec<String>,
    #[serde(default)]
    #[expect(dead_code, reason = "accepted for the protocol; the Harness draws the seed itself")]
    seed: u64,
    #[serde(default)]
    #[expect(dead_code, reason = "accepted for the protocol; the Harness stamps the start itself")]
    started_at: String,
}

/// Attest's `PromptObservation`, field for field.
#[derive(Serialize)]
struct Observation {
    status: &'static str,
    result: Option<String>,
    error_kind: Option<String>,
    error_line: Option<u32>,
    message: Option<String>,
    rounds: u32,
    host_version: String,
}

impl Observation {
    fn new(status: &'static str) -> Self {
        Self {
            status,
            result: None,
            error_kind: None,
            error_line: None,
            message: None,
            rounds: 0,
            host_version: format!("runvil@{}", env!("RUNVIL_COMMIT")),
        }
    }
}

/// Plays each model round from the script, in order, and remembers when a
/// round found the script empty.
struct ScriptBroker {
    replies: Mutex<VecDeque<String>>,
    played: AtomicU32,
    starved: AtomicBool,
}

impl ScriptBroker {
    fn new(script: Vec<String>) -> Self {
        Self {
            replies: Mutex::new(script.into()),
            played: AtomicU32::new(0),
            starved: AtomicBool::new(false),
        }
    }
}

impl InferenceBroker for ScriptBroker {
    fn models(&self) -> BoxFuture<Result<ModelCatalog, CompletionError>> {
        let catalog = ModelCatalog::new([ModelInfo::new(
            ModelId::gateway(MODEL).expect("the scripted model id is valid"),
            "Attest's scripted model: replies come from the doc, in round order",
            NonZeroU32::new(131_072).expect("131072 is non-zero"),
            ThinkingMode::Never,
        )])
        .expect("one model is a valid catalog");
        Box::pin(async move { Ok(catalog) })
    }

    fn chat(
        &self,
        _binding: ModelBinding,
        _messages: Vec<Message>,
        _tools: Vec<ToolSchema>,
        _options: CompletionOptions,
        _round: Round,
    ) -> BoxFuture<Result<Box<Completion>, CompletionError>> {
        let next = self
            .replies
            .lock()
            .expect("the script lock is never poisoned")
            .pop_front();
        let reply = match next {
            Some(text) => {
                self.played.fetch_add(1, Ordering::SeqCst);
                Completion::from_result(CompletionResult::Text(text), MODEL).map(Box::new)
            }
            None => {
                self.starved.store(true, Ordering::SeqCst);
                Err(CompletionError::new(
                    CompletionErrorKind::Rejected,
                    "this round has no scripted reply: the doc never showed what the model said",
                ))
            }
        };
        Box::pin(async move { reply })
    }
}

/// Sleeps on tokio's timer.
struct Clock;

impl Timer for Clock {
    fn sleep(&self, duration: Duration) -> BoxFuture<()> {
        Box::pin(tokio::time::sleep(duration))
    }
}

async fn observe(request: Request) -> Result<Observation, String> {
    // The Engine's parser first: a parse failure keeps its kind and line,
    // which the Harness's failed outcome folds into one message.
    let (parsed, _events) = Prompt::parse(&request.prompt, EXECUTION);
    if let Err(error) = parsed {
        let mut observation = Observation::new("parse_error");
        observation.error_kind = Some(format!("{:?}", error.kind()));
        observation.error_line = error.line().map(NonZeroU32::get);
        observation.message = Some(error.to_string());
        return Ok(observation);
    }

    let broker = Arc::new(ScriptBroker::new(request.script));
    let recorder = Arc::new(MemoryRecorder::new());
    let harness = Harness::new(
        recorder,
        Arc::clone(&broker) as Arc<dyn InferenceBroker>,
        Arc::new(Clock),
        Arc::new(HostContext::new(PackRegistry::new(), HostServices::new())),
        HostServices::new(),
    );
    let report = harness
        .run(RunRequest {
            name: EXECUTION.to_owned(),
            source: request.prompt,
            args: request.args.join(" "),
            input_text: None,
            vfs: VfsRef::default(),
            host: HostSnapshot {
                selected_model: Some(MODEL.to_owned()),
                workspace_roots: Vec::new(),
            },
        })
        .await
        .map_err(|error| harness::display_chain(&error))?;

    let rounds = broker.played.load(Ordering::SeqCst);
    let starved = broker.starved.load(Ordering::SeqCst);
    let mut observation = match report.outcome {
        RunOutcome::Completed { final_text } => {
            let mut observation = Observation::new("completed");
            observation.result = Some(final_text);
            observation
        }
        RunOutcome::Failed { kind, message } => {
            let status = if starved {
                "needs_script"
            } else if kind == "RequirementsUnmet" {
                "refused"
            } else if kind == "Parse" {
                "parse_error"
            } else {
                "failed"
            };
            let mut observation = Observation::new(status);
            observation.error_kind = Some(kind);
            observation.message = Some(message);
            observation
        }
        RunOutcome::Cancelled => {
            let mut observation = Observation::new("failed");
            observation.error_kind = Some("Cancelled".to_owned());
            observation.message = Some("the run was cancelled".to_owned());
            observation
        }
    };
    observation.rounds = rounds;
    Ok(observation)
}

#[tokio::main(flavor = "current_thread")]
async fn main() {
    let mut input = String::new();
    if let Err(error) = std::io::stdin().read_to_string(&mut input) {
        eprintln!("runvil-docs-host: cannot read the request from stdin: {error}");
        std::process::exit(2);
    }
    let request: Request = match serde_json::from_str(&input) {
        Ok(request) => request,
        Err(error) => {
            eprintln!("runvil-docs-host: the request is not valid JSON: {error}");
            std::process::exit(2);
        }
    };
    match observe(request).await {
        Ok(observation) => {
            let mut out = std::io::stdout().lock();
            let json = serde_json::to_string(&observation).expect("the observation serializes");
            if writeln!(out, "{json}").is_err() {
                std::process::exit(3);
            }
        }
        Err(error) => {
            eprintln!("runvil-docs-host: the Harness could not report an outcome: {error}");
            std::process::exit(1);
        }
    }
}
