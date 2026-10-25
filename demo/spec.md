# Acme CRM: deal workflow spec

This spec is the contract for the Deals page of Acme CRM. The app must do what it says.

Serve the app:

```bash
python -m http.server 8766 --bind 127.0.0.1 --directory "$ATTEST_APP_DIR"
```

Open http://127.0.0.1:8766/ in your browser. The page shows the heading "Deals".

Type "Globex renewal" into the "Deal name" box, type "12000" into the "Amount" box and click "Add deal". The pipeline table shows "Globex renewal".

On the "Globex renewal" row, click the "Mark as won" button. The deal's status changes to "Won".
