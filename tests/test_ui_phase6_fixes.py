"""Regressions for the two reproduced phase-6 empty-dashboard defects."""
import http.client
import json
import re
import shutil
import subprocess
import threading
import xml.etree.ElementTree as ET

from garmin_coach import dashboard


def test_favicon_is_public_without_opening_protected_routes(monkeypatch):
    monkeypatch.setattr(dashboard, "user_from_cookie", lambda cookie: None)
    monkeypatch.setenv("GARMIN_COACH_MOBILE_TOKEN", "synthetic-mobile-token")
    server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        connection.request("GET", "/favicon.ico")
        response = connection.getresponse()
        body = response.read()
        assert response.status == 200
        assert response.getheader("Content-Type") == "image/svg+xml"
        assert int(response.getheader("Content-Length")) == len(body)
        assert ET.fromstring(body).tag == "{http://www.w3.org/2000/svg}svg"
        connection.request("GET", "/api/profile")
        response = connection.getresponse()
        response.read()
        assert response.status == 401
        connection.request("GET", "/dashboard")
        response = connection.getresponse()
        response.read()
        assert response.status == 302
        assert response.getheader("Location") == "/"
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_empty_activity_replaces_loading_and_stale_zones():
    html = dashboard.TEMPLATE_FILE.read_text(encoding="utf-8")
    function = re.search(r"  function renderEmptyActivity\(\).*", html).group(0)
    node = shutil.which("node")
    assert node, "Node is required to execute the dashboard rendering regression"
    script = """
const elements = new Map();
const $ = id => {if (!elements.has(id)) elements.set(id, {}); return elements.get(id)};
""" + function + """
for (const before of ['Chargement…', '<div>Z5 : anciennes données</div>']) {
  $('zoneList').innerHTML = before;
  renderEmptyActivity();
  if ($('zoneList').innerHTML.includes('Chargement') || $('zoneList').innerHTML.includes('Z5')) throw Error('stale zones');
  if (!$('zoneList').innerHTML.includes('Aucune activité importée')) throw Error('missing empty state');
  for (const id of ['statDistance','statPace','statElevation','statCadence','statStride']) {
    if ($(id).textContent !== 'Non disponible') throw Error(id);
  }
}
console.log(JSON.stringify({latestDate: $('latestDate').textContent}));
"""
    result = subprocess.run([node, "-e", script], capture_output=True, encoding="utf-8", check=True)
    assert json.loads(result.stdout)["latestDate"] == "Aucune course importée"
