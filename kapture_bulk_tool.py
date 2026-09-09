#!/usr/bin/env python3
"""Local/hosted tool: upload a CSV (ticket_id, comment columns) and bulk-close Kapture tickets."""
import base64
import hmac
import http.server
import socketserver
import json
import csv
import io
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
import webbrowser

PORT = int(os.environ.get("PORT", 8765))
# Railway (and most PaaS) always set PORT - use that as a signal to bind all interfaces.
HOST = os.environ.get("HOST") or ("0.0.0.0" if "PORT" in os.environ else "127.0.0.1")

KAPTURE_AUTH = os.environ.get("KAPTURE_AUTH_TOKEN")  # e.g. "Basic dGRvdj..."
if not KAPTURE_AUTH:
    raise SystemExit(
        "KAPTURE_AUTH_TOKEN environment variable is not set.\n"
        "Set it to the full 'Basic <token>' value before starting the server."
    )

APP_USERNAME = os.environ.get("APP_USERNAME")
APP_PASSWORD = os.environ.get("APP_PASSWORD")
if not APP_USERNAME or not APP_PASSWORD:
    print(
        "WARNING: APP_USERNAME / APP_PASSWORD are not set - this app is running "
        "with NO login protection. Anyone who can reach it can bulk-close real "
        "tickets. Set both env vars before deploying anywhere other than your "
        "own machine."
    )

URL = "https://wiomin.kapturecrm.com/update-ticket-from-other-source.html/v.2.0?="
DELAY_SECONDS = 0.3
SSL_CONTEXT = ssl.create_default_context()

state = {
    "rows": [],
    "results": [],
    "running": False,
    "processed": 0,
    "total": 0,
}
lock = threading.Lock()


def call_kapture(ticket_id, comment, status_val, sub_status_val):
    payload = json.dumps([{
        "ticket_id": str(ticket_id),
        "status": status_val,
        "sub_status": sub_status_val,
        "comment": comment,
    }]).encode("utf-8")
    req = urllib.request.Request(
        URL,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": KAPTURE_AUTH,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as resp:
            status_code = str(resp.status)
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        status_code = str(e.code)
        body = e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return "0", str(e), False

    try:
        parsed = json.loads(body)
        message = str(parsed.get("message", body)).strip()
        ok = parsed.get("status") == "success"
    except Exception:
        message = body.strip()
        ok = False
    return status_code, message, ok


def run_bulk(rows, status_val, sub_status_val):
    with lock:
        state["running"] = True
        state["results"] = []
        state["processed"] = 0
        state["total"] = len(rows)
    for row in rows:
        http_status, message, ok = call_kapture(
            row["ticket_id"], row["comment"], status_val, sub_status_val
        )
        with lock:
            state["results"].append({
                "ticket_id": row["ticket_id"],
                "http_status": http_status,
                "message": message,
                "ok": ok,
            })
            state["processed"] += 1
        time.sleep(DELAY_SECONDS)
    with lock:
        state["running"] = False


HTML_PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Kapture Bulk Ticket Closer</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 900px; margin: 30px auto; padding: 0 16px; color: #1a1a1a; }
  h1 { font-size: 20px; }
  .card { border: 1px solid #ddd; border-radius: 8px; padding: 16px; margin-bottom: 16px; }
  label { display: block; margin: 8px 0 4px; font-weight: 600; font-size: 13px; }
  input[type=text], select { padding: 6px 8px; border: 1px solid #ccc; border-radius: 4px; width: 200px; }
  input[type=file] { margin-top: 4px; }
  button { padding: 8px 14px; border: none; border-radius: 5px; background: #2563eb; color: white; font-size: 14px; cursor: pointer; margin-right: 8px; margin-top: 8px; }
  button.secondary { background: #6b7280; }
  button.danger { background: #dc2626; }
  button:disabled { background: #9ca3af; cursor: not-allowed; }
  table { border-collapse: collapse; width: 100%; margin-top: 10px; font-size: 13px; }
  th, td { border: 1px solid #e5e7eb; padding: 5px 8px; text-align: left; }
  th { background: #f3f4f6; }
  tr.ok { background: #f0fdf4; }
  tr.fail { background: #fef2f2; }
  .muted { color: #6b7280; font-size: 13px; }
  #progressBarOuter { background: #e5e7eb; border-radius: 6px; height: 10px; margin-top: 10px; overflow: hidden; }
  #progressBarInner { background: #2563eb; height: 100%; width: 0%; transition: width 0.2s; }
  .row { display: flex; gap: 24px; flex-wrap: wrap; }
  pre.anomalies { background: #fffbeb; border: 1px solid #fde68a; padding: 8px; border-radius: 6px; max-height: 150px; overflow: auto; font-size: 12px; }
</style>
</head>
<body>
<h1>Kapture Bulk Ticket Closer</h1>
<p class="muted">Upload a CSV with a ticket_id column and a comment column. Preview it, run a single test, then run the full batch.</p>

<div class="card">
  <label>CSV file</label>
  <input type="file" id="csvFile" accept=".csv">
  <button id="sampleBtn" class="secondary" type="button">Download Sample CSV</button>
  <label><input type="checkbox" id="hasHeader" checked> File has a header row</label>
  <div class="row">
    <div>
      <label>Ticket ID column</label>
      <select id="tidCol"></select>
    </div>
    <div>
      <label>Comment column</label>
      <select id="commentCol"></select>
    </div>
    <div>
      <label>status</label>
      <input type="text" id="statusVal" value="Complete">
    </div>
    <div>
      <label>sub_status</label>
      <input type="text" id="subStatusVal" value="CO">
    </div>
  </div>
  <button id="previewBtn">Preview / Validate</button>
</div>

<div class="card" id="previewCard" style="display:none">
  <h3>Preview</h3>
  <div id="previewSummary" class="muted"></div>
  <div id="anomaliesBox"></div>
  <table id="previewTable"><thead><tr><th>ticket_id</th><th>comment</th></tr></thead><tbody></tbody></table>
  <button id="testBtn" class="secondary">Test First Row Only</button>
  <button id="runAllBtn" class="danger">Run All (Bulk Close)</button>
</div>

<div class="card" id="resultsCard" style="display:none">
  <h3>Results</h3>
  <div id="progressSummary" class="muted"></div>
  <div id="progressBarOuter"><div id="progressBarInner"></div></div>
  <table id="resultsTable"><thead><tr><th>ticket_id</th><th>http_status</th><th>ok</th><th>message</th></tr></thead><tbody></tbody></table>
  <button id="downloadBtn" class="secondary">Download results CSV</button>
</div>

<script>
let csvText = null;
let parsedRows = null; // array of arrays
let pollTimer = null;

document.getElementById('csvFile').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  csvText = await file.text();
  parsedRows = parseCSV(csvText);
  populateColumnSelectors(parsedRows);
});

function parseCSV(text) {
  // simple CSV parser handling quoted multi-line fields
  const rows = [];
  let row = [], field = '', inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i+1] === '"') { field += '"'; i++; }
        else { inQuotes = false; }
      } else field += c;
    } else {
      if (c === '"') inQuotes = true;
      else if (c === ',') { row.push(field); field = ''; }
      else if (c === '\\n') { row.push(field); rows.push(row); row = []; field = ''; }
      else if (c === '\\r') { /* skip */ }
      else field += c;
    }
  }
  if (field.length || row.length) { row.push(field); rows.push(row); }
  return rows.filter(r => r.length && r.some(c => c.trim() !== ''));
}

function populateColumnSelectors(rows) {
  if (!rows.length) return;
  const hasHeader = document.getElementById('hasHeader').checked;
  const headerRow = rows[0];
  const tidSel = document.getElementById('tidCol');
  const commentSel = document.getElementById('commentCol');
  tidSel.innerHTML = ''; commentSel.innerHTML = '';
  headerRow.forEach((h, idx) => {
    const label = hasHeader ? (h || ('Column ' + (idx+1))) : ('Column ' + (idx+1));
    const opt1 = new Option(label, idx);
    const opt2 = new Option(label, idx);
    tidSel.add(opt1);
    commentSel.add(opt2);
  });
  if (headerRow.length > 1) commentSel.value = 1;
}

document.getElementById('hasHeader').addEventListener('change', () => {
  if (parsedRows) populateColumnSelectors(parsedRows);
});

document.getElementById('previewBtn').addEventListener('click', async () => {
  if (!csvText) { alert('Choose a CSV file first'); return; }
  const body = {
    csv: csvText,
    has_header: document.getElementById('hasHeader').checked,
    ticket_col: document.getElementById('tidCol').value,
    comment_col: document.getElementById('commentCol').value,
  };
  const resp = await fetch('/upload', { method: 'POST', body: JSON.stringify(body) });
  const data = await resp.json();
  if (data.error) { alert(data.error); return; }
  document.getElementById('previewCard').style.display = 'block';
  document.getElementById('previewSummary').textContent =
    `${data.total} valid rows detected` + (data.anomaly_count ? `, ${data.anomaly_count} skipped (see below)` : '');
  const anomBox = document.getElementById('anomaliesBox');
  anomBox.innerHTML = '';
  if (data.anomalies && data.anomalies.length) {
    const pre = document.createElement('pre');
    pre.className = 'anomalies';
    pre.textContent = data.anomalies.join('\\n');
    anomBox.appendChild(pre);
  }
  const tbody = document.querySelector('#previewTable tbody');
  tbody.innerHTML = '';
  data.preview.forEach(r => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${escapeHtml(r.ticket_id)}</td><td>${escapeHtml(r.comment).slice(0,200)}</td>`;
    tbody.appendChild(tr);
  });
});

function escapeHtml(s) {
  return (s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

document.getElementById('testBtn').addEventListener('click', () => runJob('test'));
document.getElementById('runAllBtn').addEventListener('click', () => {
  if (!confirm('This will send real close requests to ALL previewed tickets. Continue?')) return;
  runJob('all');
});

async function runJob(mode) {
  const body = {
    mode,
    status: document.getElementById('statusVal').value,
    sub_status: document.getElementById('subStatusVal').value,
  };
  const resp = await fetch('/run', { method: 'POST', body: JSON.stringify(body) });
  const data = await resp.json();
  if (data.error) { alert(data.error); return; }
  document.getElementById('resultsCard').style.display = 'block';
  document.querySelector('#resultsTable tbody').innerHTML = '';
  startPolling();
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    const resp = await fetch('/progress');
    const data = await resp.json();
    document.getElementById('progressSummary').textContent =
      `${data.processed} / ${data.total} processed` + (data.running ? ' (running...)' : ' (done)');
    const pct = data.total ? Math.round(100 * data.processed / data.total) : 0;
    document.getElementById('progressBarInner').style.width = pct + '%';
    const tbody = document.querySelector('#resultsTable tbody');
    tbody.innerHTML = '';
    data.results.slice().reverse().forEach(r => {
      const tr = document.createElement('tr');
      tr.className = r.ok ? 'ok' : 'fail';
      tr.innerHTML = `<td>${escapeHtml(r.ticket_id)}</td><td>${escapeHtml(r.http_status)}</td><td>${r.ok}</td><td>${escapeHtml(r.message)}</td>`;
      tbody.appendChild(tr);
    });
    if (!data.running) clearInterval(pollTimer);
  }, 1000);
}

document.getElementById('downloadBtn').addEventListener('click', () => {
  window.location = '/download';
});

document.getElementById('sampleBtn').addEventListener('click', () => {
  window.location = '/sample-csv';
});
</script>
</body>
</html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    def _send_json(self, obj, code=200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self):
        if not APP_USERNAME or not APP_PASSWORD:
            return True  # no credentials configured -> auth disabled (local dev only)
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
            user, _, pwd = decoded.partition(":")
        except Exception:
            return False
        return hmac.compare_digest(user, APP_USERNAME) and hmac.compare_digest(pwd, APP_PASSWORD)

    def _require_auth(self):
        body = b"Authentication required"
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Kapture Bulk Tool"')
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._authorized():
            self._require_auth()
            return
        if self.path == "/" or self.path == "":
            data = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path.startswith("/progress"):
            with lock:
                self._send_json({
                    "running": state["running"],
                    "processed": state["processed"],
                    "total": state["total"],
                    "results": state["results"],
                })
        elif self.path.startswith("/sample-csv"):
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["ticket_id", "comment"])
            writer.writerow(["787972874664", "Customer VOC: Call not required as PING is UP"])
            writer.writerow(["787989910841", "Action taken by PFT: Checked WIOM hub and PING is UP"])
            data = buf.getvalue().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Disposition", "attachment; filename=sample_tickets.csv")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path.startswith("/download"):
            with lock:
                results = list(state["results"])
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["ticket_id", "http_status", "ok", "message"])
            for r in results:
                writer.writerow([r["ticket_id"], r["http_status"], r["ok"], r["message"]])
            data = buf.getvalue().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Disposition", "attachment; filename=kapture_bulk_results.csv")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if not self._authorized():
            self._require_auth()
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
        except Exception:
            self._send_json({"error": "Bad request body"}, 400)
            return

        if self.path == "/upload":
            csv_text = payload.get("csv", "")
            has_header = payload.get("has_header", True)
            try:
                tid_idx = int(payload.get("ticket_col", 0))
                comment_idx = int(payload.get("comment_col", 1))
            except (TypeError, ValueError):
                self._send_json({"error": "Invalid column selection"}, 400)
                return

            reader = csv.reader(io.StringIO(csv_text))
            all_rows = [r for r in reader if any(c.strip() for c in r)]
            if not all_rows:
                self._send_json({"error": "Empty file"}, 400)
                return
            data_rows = all_rows[1:] if has_header else all_rows

            rows = []
            anomalies = []
            for r in data_rows:
                if len(r) <= max(tid_idx, comment_idx):
                    anomalies.append(f"Row skipped (too few columns): {r}")
                    continue
                tid = r[tid_idx].strip()
                comment = r[comment_idx].strip()
                if not tid:
                    continue
                if not tid.isdigit():
                    anomalies.append(f"Non-numeric ticket id skipped: {tid!r}")
                    continue
                rows.append({"ticket_id": tid, "comment": comment})

            with lock:
                state["rows"] = rows

            self._send_json({
                "total": len(rows),
                "preview": rows[:8],
                "anomalies": anomalies[:30],
                "anomaly_count": len(anomalies),
            })

        elif self.path == "/run":
            mode = payload.get("mode", "all")
            status_val = payload.get("status", "Complete")
            sub_status_val = payload.get("sub_status", "CO")
            with lock:
                rows = list(state["rows"])
                running = state["running"]
            if running:
                self._send_json({"error": "A job is already running"}, 400)
                return
            if not rows:
                self._send_json({"error": "No data uploaded yet"}, 400)
                return
            target_rows = rows[:1] if mode == "test" else rows
            t = threading.Thread(target=run_bulk, args=(target_rows, status_val, sub_status_val), daemon=True)
            t.start()
            self._send_json({"started": True, "count": len(target_rows)})
        else:
            self._send_json({"error": "Not found"}, 404)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((HOST, PORT), Handler) as httpd:
        url = f"http://{HOST}:{PORT}/"
        print(f"Kapture Bulk Ticket Closer running at {url}")
        if HOST in ("127.0.0.1", "localhost"):
            try:
                webbrowser.open(url)
            except Exception:
                pass
        httpd.serve_forever()
