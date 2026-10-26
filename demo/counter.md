# Counter

Create a minimal counter page and start a local server:

```bash
cat > index.html <<'EOF'
<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>Counter</title>
<h1>Counter</h1>
<button id="counter">count is 0</button>
<script>
let count = 0;
document.querySelector("#counter").addEventListener("click", () => {
  count += 2;
  document.querySelector("#counter").textContent = `count is ${count}`;
});
</script>
</html>
EOF
python -m http.server 8765 --bind 127.0.0.1
```

Open http://127.0.0.1:8765/ in your browser. The page shows the heading "Counter" and a button labelled "count is 0".

Click the "count is 0" button once. The counter changes: its label changes to "count is 1".
