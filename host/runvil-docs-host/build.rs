//! Bakes the Runvil commit the Host was built from into the binary, so every
//! PromptObservation's `host_version` names it.

use std::path::Path;
use std::process::Command;

fn main() {
    let runvil = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../runvil");
    let commit = Command::new("git")
        .args(["-C", &runvil.display().to_string(), "rev-parse", "--short=12", "HEAD"])
        .output()
        .ok()
        .filter(|out| out.status.success())
        .map(|out| String::from_utf8_lossy(&out.stdout).trim().to_owned())
        .filter(|sha| !sha.is_empty())
        .unwrap_or_else(|| "unknown".to_owned());
    println!("cargo:rustc-env=RUNVIL_COMMIT={commit}");
    println!("cargo:rerun-if-changed={}", runvil.join(".git/HEAD").display());
    println!("cargo:rerun-if-changed=build.rs");
}
