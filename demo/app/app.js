// Acme CRM demo: deals live in memory for the session.
const deals = [
  { name: "Initech platform upgrade", owner: "Dana Reyes", amount: 48000, status: "Open" },
  { name: "Umbrella security audit", owner: "Sam Ortiz", amount: 22500, status: "Open" },
];

const money = (n) => "$" + n.toLocaleString("en-US");

function render() {
  const body = document.getElementById("deals");
  body.innerHTML = "";
  for (const deal of deals) {
    const row = document.createElement("tr");
    row.innerHTML = `<td>${deal.name}</td><td>${deal.owner}</td><td class="num">${money(deal.amount)}</td>` +
      `<td><span class="pill ${deal.status.toLowerCase()}">${deal.status}</span></td>`;
    body.appendChild(row);
  }
  const open = deals.filter((d) => d.status === "Open");
  const won = deals.filter((d) => d.status === "Won");
  document.getElementById("kpi-open").textContent = money(open.reduce((s, d) => s + d.amount, 0));
  document.getElementById("kpi-won").textContent = money(won.reduce((s, d) => s + d.amount, 0));
  document.getElementById("kpi-count").textContent = String(open.length);
}

document.getElementById("deal-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const name = document.getElementById("deal-name").value.trim();
  const amount = Number(document.getElementById("deal-amount").value.replace(/[^0-9]/g, ""));
  if (!name || !amount) return;
  deals.push({ name, owner: "Dana Reyes", amount, status: "Open" });
  event.target.reset();
  render();
});

render();
