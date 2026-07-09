"""Cross-node Docker visibility routes (#3763).

GET /dashboard/api/fleet/containers -> aggregated container JSON across the mesh.
GET /dashboard/fleet-docker         -> a simple page rendering that JSON.
"""
from flask import jsonify, Response
from flask_login import login_required

from ..services.fleet_docker_service import get_fleet_containers


def register_fleet_docker_routes(bp, admin_required_decorator):

    @bp.route("/api/fleet/containers")
    @login_required
    @admin_required_decorator
    def api_fleet_containers():
        try:
            return jsonify(get_fleet_containers())
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/fleet-docker")
    @login_required
    @admin_required_decorator
    def fleet_docker_page():
        # Self-contained page; all dynamic values rendered via textContent (XSS-safe).
        return Response(_PAGE, mimetype="text/html")


_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Fleet Docker — Cross-Node Containers</title>
<style>
 body{font-family:'Segoe UI',sans-serif;background:#0a0a0f;color:#c9d1d9;margin:0;padding:20px}
 h1{font-size:1.1rem;letter-spacing:2px;text-transform:uppercase;color:#00ffff}
 .meta{color:#8a93a1;font-size:.8rem;margin-bottom:16px}
 .node{border:1px solid rgba(0,255,255,.2);border-radius:8px;margin:0 0 14px;overflow:hidden}
 .nhead{display:flex;justify-content:space-between;align-items:center;padding:8px 12px;background:rgba(0,255,255,.06)}
 .nname{font-weight:600;color:#00ffff}.noff{color:#cc2244}.non{color:#00aa33}
 table{width:100%;border-collapse:collapse;font-size:.8rem}
 td,th{text-align:left;padding:5px 12px;border-top:1px solid rgba(255,255,255,.05)}
 th{color:#8a93a1;font-weight:500}.st-running{color:#00aa33}.st-other{color:#eab308}
 .err{color:#cc2244;padding:6px 12px;font-size:.78rem}
</style></head><body>
<h1>Fleet Docker — Cross-Node Containers</h1>
<div class="meta" id="meta">Loading…</div>
<div id="nodes"></div>
<script>
function esc(s){var d=document.createElement('div');d.textContent=(s==null?'':String(s));return d.innerHTML;}
fetch('/dashboard/api/fleet/containers').then(function(r){return r.json();}).then(function(d){
  if(!d.success){document.getElementById('meta').textContent='Error: '+(d.error||'unknown');return;}
  document.getElementById('meta').textContent=d.online_count+'/'+d.node_count+' nodes online · '+d.total_containers+' containers';
  var out='';
  (d.nodes||[]).forEach(function(n){
    out+='<div class="node"><div class="nhead"><span class="nname">'+esc(n.node)+' <span style="color:#8a93a1;font-weight:400">('+esc(n.host)+')</span></span>'+
         '<span class="'+(n.online?'non':'noff')+'">'+(n.online?'● online · '+(n.containers||[]).length:'○ offline')+'</span></div>';
    if(n.error){out+='<div class="err">'+esc(n.error)+'</div>';}
    if((n.containers||[]).length){
      out+='<table><thead><tr><th>Container</th><th>Image</th><th>State</th><th>Status</th></tr></thead><tbody>';
      n.containers.forEach(function(c){
        var st=(c.state==='running')?'st-running':'st-other';
        out+='<tr><td>'+esc(c.name)+'</td><td>'+esc(c.image)+'</td><td class="'+st+'">'+esc(c.state)+'</td><td>'+esc(c.status)+'</td></tr>';
      });
      out+='</tbody></table>';
    }
    out+='</div>';
  });
  document.getElementById('nodes').innerHTML=out;
}).catch(function(e){document.getElementById('meta').textContent='Fetch failed: '+e;});
</script></body></html>"""
