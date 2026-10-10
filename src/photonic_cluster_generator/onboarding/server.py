from __future__ import annotations

from pathlib import Path
import threading

from ..models import SpecError
from .registry import DeviceRegistry, load_registry_catalog
from .scanner import DeviceScan, scan_gds_folder


_PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GDS 器件端口注册</title>
<style>
:root{font-family:Inter,"Microsoft YaHei",sans-serif;color:#e8edf4;background:#10151c}*{box-sizing:border-box}
body{margin:0;height:100vh;display:grid;grid-template-columns:330px 1fr;background:#10151c}.side{padding:16px;border-right:1px solid #2b3542;overflow:auto}.main{display:grid;grid-template-rows:auto 1fr auto;min-width:0}.title{font-size:18px;font-weight:700;margin-bottom:8px}.module-nav{display:flex;gap:4px}.module-nav a{flex:1;text-align:center;color:#9caabc;text-decoration:none;padding:7px;border:1px solid transparent;border-radius:6px;font-size:13px}.module-nav a:hover{border-color:#435064}.module-nav a.active{color:#fff;background:#1c2a38;border-color:#4777a8}.muted{color:#9caabc;font-size:12px}.search{width:100%;margin:14px 0 8px;padding:9px;background:#19212b;color:#fff;border:1px solid #344050;border-radius:7px}.device{padding:10px;margin:4px 0;border-radius:7px;cursor:pointer;border:1px solid transparent}.device:hover,.device.active{background:#1c2733;border-color:#4777a8}.device b{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.badge{display:inline-block;margin-left:6px;padding:1px 5px;border-radius:8px;background:#195f43;color:#b7f7d7;font-size:10px}.bar{padding:12px 18px;border-bottom:1px solid #2b3542;display:flex;gap:12px;align-items:center;flex-wrap:wrap}.chip{padding:5px 9px;border:1px solid #435064;border-radius:999px;font-size:12px;cursor:pointer}.chip.off{opacity:.28}.workspace{display:grid;grid-template-columns:minmax(0,1fr) 390px;min-height:0}.canvas{background:#0a0e13;min-width:0;overflow:hidden;position:relative}.canvas svg{width:100%;height:100%}.panel{padding:12px;border-left:1px solid #2b3542;overflow:auto}.candidate{display:grid;grid-template-columns:24px 1fr 54px;gap:7px;align-items:center;padding:6px;border-bottom:1px solid #26313e;font-size:12px}.candidate input[type=text]{width:70px}.candidate.selected{background:#17364a}.footer{padding:12px 18px;border-top:1px solid #2b3542;display:flex;gap:10px;align-items:center}.footer input{padding:8px;background:#19212b;color:#fff;border:1px solid #445166;border-radius:6px}.footer button{padding:9px 15px;background:#2d76b5;color:#fff;border:0;border-radius:6px;font-weight:700;cursor:pointer}.footer button:disabled{opacity:.4}.status{font-size:12px}.port{cursor:pointer;stroke:#fff;stroke-width:.12;vector-effect:non-scaling-stroke}.port:hover{stroke-width:.28}.port.selected{stroke:#ffdc62;stroke-width:.35}.legend{display:flex;gap:8px;flex-wrap:wrap}.swatch{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px}input,button{font:inherit}h3{font-size:14px;margin:10px 0}.empty{padding:30px;color:#9caabc}.sourcebox{margin:14px 0 10px;padding:10px;border:1px solid #344050;border-radius:8px;background:#151d27}.sourcebox label{display:block;font-size:12px;font-weight:700;margin-bottom:6px}.sourcebox input{width:100%;padding:8px;background:#0f151d;color:#fff;border:1px solid #435064;border-radius:6px}.sourcebox button{width:100%;margin-top:7px;padding:8px;border:0;border-radius:6px;background:#2d76b5;color:#fff;font-weight:700;cursor:pointer}.sourcebox button:disabled{opacity:.45}.sourcebox .muted{margin-top:7px;overflow-wrap:anywhere}.error{color:#ff9a9a}
.main{height:100vh;min-height:0;overflow:hidden;grid-template-rows:auto minmax(0,1fr) auto}.workspace{min-height:0;overflow:hidden}.canvas,.panel{min-height:0}.canvas svg{cursor:grab}.canvas svg.panning{cursor:grabbing}.view-tools{display:flex;gap:5px;margin-left:auto}.view-tools button{padding:4px 9px;background:#19212b;color:#fff;border:1px solid #435064;border-radius:6px;cursor:pointer}
body{grid-template-rows:58px minmax(0,1fr);overflow:hidden}header{grid-column:1/3;grid-row:1;border-bottom:1px solid #2b3542;display:flex;align-items:center;gap:12px;padding:9px 14px;background:#111821}header h1{font-size:17px;margin:0 4px 0 0;white-space:nowrap}header .module-nav{margin-right:10px}header .module-nav a{flex:none;padding:7px 10px}.side{grid-column:1;grid-row:2}.main{grid-column:2;grid-row:2;height:100%;}.side>.sourcebox{margin-top:0}
</style></head><body>
<header><h1>光子版图工作台</h1><nav class="module-nav"><a href="/">版图装配</a><a class="active" href="/register">器件注册</a></nav></header>
<aside class="side"><div class="sourcebox"><label for="sourceFolder">GDS 源目录</label><input id="sourceFolder" placeholder="输入文件夹绝对路径"><button id="loadSource">扫描目录</button><div class="muted" id="registryInfo"></div></div><div class="muted" id="summary">读取中…</div><input class="search" id="search" placeholder="筛选 GDS"><div id="devices"></div></aside>
<main class="main"><div class="bar"><b id="deviceTitle">请选择器件</b><span class="muted" id="meta"></span><div class="legend" id="layers"></div><div class="view-tools"><button id="zoomOut" title="缩小">−</button><button id="zoomIn" title="放大">＋</button><button id="fitDevice">适配</button></div></div>
<div class="workspace"><div class="canvas" id="canvas"><div class="empty">选择 GDS 查看几何与端口候选。</div></div><section class="panel"><h3>候选端口</h3><div class="muted">选择真实端口；黄色表示已选。</div><p><button id="manual">＋ 添加端口</button></p><div class="muted" id="candidateSummary"></div><div id="candidates"></div></section></div>
<div class="footer"><label>模板 ID <input id="templateId" size="24"></label><label>器件族 <input id="family" list="familyOptions" size="20" value="unclassified_device"><datalist id="familyOptions"></datalist></label><button id="save" disabled>保存注册</button><span class="status" id="status"></span></div></main>
<script>
const colors=['#4cc9f0','#f72585','#b8f35a','#f7b267','#b388ff','#52d6a6','#ffdf6b','#e879f9','#7dd3fc','#fb7185'];
let devices=[], current=null, selected=new Map(), visible=new Set(), registryTemplates=new Set(), registryFamilies=[], deviceView=null, viewPan=null;
const q=s=>document.querySelector(s); const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function slug(s){return s.replace(/\.gds$/i,'').normalize('NFKD').replace(/[^A-Za-z0-9_]+/g,'_').replace(/^([^A-Za-z_])/,'d_$1').replace(/^_+|_+$/g,'').slice(0,60)||'device'}
async function api(url,opts){const r=await fetch(url,opts); const j=await r.json(); if(!r.ok)throw Error(j.error||r.statusText); return j}
function showState(state){q('#sourceFolder').value=state.source_folder||'';q('#registryInfo').textContent=`注册库：${state.registry_folder}`;q('#summary').classList.remove('error');q('#summary').textContent=state.source_folder?`GDS：${state.device_count} · 已注册：${state.registered_template_count}`:`未选择源目录 · 已注册：${state.registered_template_count}`}
async function refreshDevices(){devices=await api('/api/devices');renderDevices()}
function renderFamilyOptions(){q('#familyOptions').innerHTML=registryFamilies.map(family=>`<option value="${esc(family)}"></option>`).join('')}
async function init(){const [state,registry]=await Promise.all([api('/api/state'),api('/api/registry')]);registryTemplates=new Set(registry.template_ids);registryFamilies=[...new Set(['unclassified_device',...(registry.families||[])])].sort();renderFamilyOptions();showState(state);await refreshDevices()}
function resetDeviceView(){current=null;deviceView=null;viewPan=null;selected.clear();visible.clear();q('#deviceTitle').textContent='请选择器件';q('#meta').textContent='';q('#layers').innerHTML='';q('#canvas').innerHTML='<div class="empty">选择 GDS 查看几何与端口候选。</div>';q('#candidates').innerHTML='';q('#candidateSummary').textContent='';q('#save').disabled=true;q('#status').textContent=''}
async function loadSource(){const source=q('#sourceFolder').value.trim(),button=q('#loadSource');if(!source){q('#summary').classList.add('error');q('#summary').textContent='请输入源目录';return}button.disabled=true;q('#summary').classList.remove('error');q('#summary').textContent='扫描中…';try{const state=await api('/api/source',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source_folder:source})});resetDeviceView();await refreshDevices();showState(state)}catch(e){q('#summary').classList.add('error');q('#summary').textContent='扫描失败：'+e.message}finally{button.disabled=false}}
function hasSiblingTop(d){return devices.some(x=>x.device_id!==d.device_id&&x.relative_path===d.relative_path)}
function deviceLabel(d){return d.relative_path+(hasSiblingTop(d)?` · ${d.top_cell}`:'')}
function renderDevices(){const needle=q('#search').value.toLowerCase(),matched=devices.filter(d=>(d.relative_path+' '+d.top_cell).toLowerCase().includes(needle));q('#devices').innerHTML=matched.length?matched.map(d=>`<div class="device ${current&&current.device_id===d.device_id?'active':''}" data-id="${d.device_id}"><b>${esc(deviceLabel(d))}${d.registered?'<span class="badge">已注册</span>':''}</b><span class="muted">${d.source_layers.map(x=>x.join('/')).join(' · ')} · 候选 ${d.candidate_count}</span></div>`).join(''):`<div class="empty">${devices.length?'无匹配结果':'请扫描 GDS 目录'}</div>`;document.querySelectorAll('.device').forEach(x=>x.onclick=()=>loadDevice(x.dataset.id))}
function loadSavedRegistration(reg){Object.entries(reg.ports||{}).forEach(([name,p])=>{let id=p.candidate_id,source=current.candidates.find(c=>c.candidate_id===id);if(!source){id='registered_'+name;source={candidate_id:id,layer:p.layer,center_um:p.center_um,width_um:p.width_um,orientation_deg:p.orientation_deg,confidence:1,reasons:['previously_registered']};current.candidates.push(source)}selected.set(id,{...source,...p,candidate_id:id,name})})}
async function loadDevice(id){current=await api('/api/device?id='+encodeURIComponent(id));deviceView=null;viewPan=null;selected.clear();visible=new Set(Object.keys(current.preview_polygons));q('#deviceTitle').textContent=deviceLabel(current);q('#meta').textContent=`顶层 ${current.top_cell} · 图形 ${current.polygon_count} · 单元 ${current.hierarchy_cell_count}`;const saved=(current.registrations||[])[0],base=current.relative_path.split('/').pop(),suggested=hasSiblingTop(current)?base.replace(/\.gds$/i,'')+'_'+current.top_cell:base;q('#templateId').value=saved?saved.template_id:slug(suggested);q('#family').value=saved?saved.family:'unclassified_device';if(saved){loadSavedRegistration(saved);q('#status').textContent=`已注册：${saved.template_id}${current.registrations.length>1?'（显示首个模板）':''}`}else q('#status').textContent='未注册';q('#save').disabled=false;renderDevices();renderAll()}
function renderAll(){renderLayers();renderCanvas();renderCandidates()}
function renderLayers(){q('#layers').innerHTML=Object.keys(current.preview_polygons).map((k,i)=>`<span class="chip ${visible.has(k)?'':'off'}" data-layer="${k}"><i class="swatch" style="background:${colors[i%colors.length]}"></i>${k}</span>`).join('');document.querySelectorAll('.chip').forEach(x=>x.onclick=()=>{visible.has(x.dataset.layer)?visible.delete(x.dataset.layer):visible.add(x.dataset.layer);renderAll()})}
const CANDIDATE_RADIUS_PX=3;
function fittedDeviceView(){const [xmin,ymin,xmax,ymax]=current.bbox_um,w=Math.max(xmax-xmin,.001),h=Math.max(ymax-ymin,.001),px=Math.max(w*.04,.35),py=Math.max(h*.08,.35);return[xmin-px,-ymax-py,w+2*px,h+2*py]}
function markerRadius(svg,pixels){const v=svg.viewBox.baseVal,scale=Math.min(Math.max(svg.clientWidth,1)/Math.max(v.width,1e-9),Math.max(svg.clientHeight,1)/Math.max(v.height,1e-9));return pixels/Math.max(scale,1e-9)}
function updateCandidateMarkers(svg=q('#deviceSvg')){if(!svg)return;const radius=markerRadius(svg,CANDIDATE_RADIUS_PX);svg.querySelectorAll('.port').forEach(node=>node.setAttribute('r',radius))}
function setDeviceView(next){deviceView=next;const svg=q('#deviceSvg');if(!svg)return;svg.setAttribute('viewBox',deviceView.join(' '));updateCandidateMarkers(svg)}
function fitDeviceView(){if(!current)return;setDeviceView(fittedDeviceView())}
function zoomDevice(factor,clientX=null,clientY=null){const svg=q('#deviceSvg');if(!svg||!current)return;const base=fittedDeviceView(),v=deviceView||base,nextWidth=v[2]*factor;if(nextWidth<base[2]*.002||nextWidth>base[2]*50)return;let x=v[0]+v[2]/2,y=v[1]+v[3]/2;if(clientX!==null){const point=svg.createSVGPoint();point.x=clientX;point.y=clientY;const local=point.matrixTransform(svg.getScreenCTM().inverse());x=local.x;y=local.y}setDeviceView([x-(x-v[0])*factor,y-(y-v[1])*factor,v[2]*factor,v[3]*factor])}
function bindDeviceView(svg){svg.onwheel=e=>{e.preventDefault();zoomDevice(e.deltaY>0?1.15:.87,e.clientX,e.clientY)};svg.onpointerdown=e=>{if(e.button!==0||e.target.classList.contains('port'))return;viewPan={id:e.pointerId,x:e.clientX,y:e.clientY,view:[...deviceView]};svg.setPointerCapture(e.pointerId);svg.classList.add('panning')};svg.onpointermove=e=>{if(!viewPan||viewPan.id!==e.pointerId)return;const dx=(e.clientX-viewPan.x)*viewPan.view[2]/Math.max(svg.clientWidth,1),dy=(e.clientY-viewPan.y)*viewPan.view[3]/Math.max(svg.clientHeight,1);setDeviceView([viewPan.view[0]-dx,viewPan.view[1]-dy,viewPan.view[2],viewPan.view[3]])};const stop=e=>{if(!viewPan||viewPan.id!==e.pointerId)return;viewPan=null;svg.classList.remove('panning')};svg.onpointerup=stop;svg.onpointercancel=stop}
function renderCanvas(){if(!deviceView)deviceView=fittedDeviceView();let shapes='',ports='';Object.entries(current.preview_polygons).forEach(([layer,polys],i)=>{if(!visible.has(layer))return;const d=polys.map(p=>p.length?`M${p.map(x=>x[0]+' '+(-x[1])).join('L')}Z`:'').join('');shapes+=`<path d="${d}" fill="${colors[i%colors.length]}" fill-opacity=".35" stroke="${colors[i%colors.length]}" stroke-width=".08" vector-effect="non-scaling-stroke"/>`});current.candidates.forEach(c=>{const key=c.layer.join('/');if(!visible.has(key))return;ports+=`<circle class="port ${selected.has(c.candidate_id)?'selected':''}" data-id="${c.candidate_id}" cx="${c.center_um[0]}" cy="${-c.center_um[1]}" r="1" fill="${selected.has(c.candidate_id)?'#ffbd24':'#ef4444'}"><title>${key} (${c.center_um.join(', ')}) w=${c.width_um} θ=${c.orientation_deg}°</title></circle>`});q('#canvas').innerHTML=`<svg id="deviceSvg" viewBox="${deviceView.join(' ')}" preserveAspectRatio="xMidYMid meet">${shapes}${ports}</svg>`;const svg=q('#deviceSvg');bindDeviceView(svg);updateCandidateMarkers(svg);document.querySelectorAll('.port').forEach(x=>{x.onpointerdown=e=>e.stopPropagation();x.onclick=()=>toggle(x.dataset.id)})}
function nextIndexedName(prefix,names){const used=new Set(names.map(String));let index=1;while(used.has(prefix+index))index++;return prefix+index}
function nextPortName(){return nextIndexedName('o',[...selected.values()].map(p=>String(p.name).trim()))}
function toggle(id){const c=current.candidates.find(x=>x.candidate_id===id);if(selected.has(id))selected.delete(id);else selected.set(id,{...c,name:nextPortName(),route_profile:'layer_'+c.layer.join('_')});renderCanvas();renderCandidates()}
function portNameError(){const names=[...selected.values()].map(p=>String(p.name).trim());if(names.some(n=>!/^[A-Za-z_][A-Za-z0-9_]*$/.test(n)))return'端口名必须是合法标识符';if(new Set(names).size!==names.length)return'端口名不能重复';return''}
function renderCandidates(){const all=current.candidates.filter(c=>visible.has(c.layer.join('/'))),shown=all.filter((c,i)=>i<500||selected.has(c.candidate_id));q('#candidateSummary').textContent=`显示 ${shown.length}/${all.length}`;q('#candidates').innerHTML=shown.map(c=>{const s=selected.get(c.candidate_id);return `<div class="candidate ${s?'selected':''}"><input type="checkbox" data-toggle="${c.candidate_id}" ${s?'checked':''}><div><b>${c.layer.join('/')} · (${c.center_um.join(', ')})</b><br><span class="muted">宽 ${c.width_um} μm · ${c.orientation_deg}° · 置信度 ${c.confidence}</span></div>${s?`<input type="text" value="${esc(s.name)}" data-name="${c.candidate_id}" title="端口名">`:''}</div>`}).join('');document.querySelectorAll('[data-toggle]').forEach(x=>x.onchange=()=>toggle(x.dataset.toggle));document.querySelectorAll('[data-name]').forEach(x=>x.oninput=()=>{selected.get(x.dataset.name).name=x.value;const error=portNameError();q('#status').textContent=error||'端口名已更新'})}
q('#loadSource').onclick=loadSource;q('#sourceFolder').onkeydown=e=>{if(e.key==='Enter')loadSource()};
q('#zoomIn').onclick=()=>zoomDevice(.8);q('#zoomOut').onclick=()=>zoomDevice(1.25);q('#fitDevice').onclick=fitDeviceView;window.addEventListener('resize',()=>updateCandidateMarkers());
q('#search').oninput=renderDevices;q('#manual').onclick=()=>{if(!current)return;const layer=prompt('层/数据类型，例如 275/0');if(!layer)return;const values=prompt('x, y, width(μm), orientation(0/90/180/270)');if(!values)return;const lv=layer.split('/').map(Number),v=values.split(',').map(Number);if(lv.length!==2||v.length!==4||[...lv,...v].some(Number.isNaN)){alert('格式错误');return}const id='manual_'+Date.now();const c={candidate_id:id,layer:lv,center_um:[v[0],v[1]],width_um:v[2],orientation_deg:v[3],confidence:0,reasons:['human_manual']};current.candidates.push(c);visible.add(layer);selected.set(id,{...c,name:nextPortName(),route_profile:'layer_'+lv.join('_')});renderAll()};q('#save').onclick=async()=>{try{const nameError=portNameError();if(nameError)throw Error(nameError);const templateId=q('#templateId').value.trim(),family=q('#family').value.trim();if(!family)throw Error('器件族不能为空');const replace=registryTemplates.has(templateId);if(replace&&!confirm(`模板 ${templateId} 已存在。确认用当前端口和器件族生成新 revision？旧记录会完整归档。`)){q('#status').textContent='已取消更新';return}q('#status').textContent='正在校验几何并保存…';const body={device_id:current.device_id,template_id:templateId,family,ports:[...selected.values()],replace_existing:replace};const result=await api('/api/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});registryTemplates.add(templateId);if(!registryFamilies.includes(family)){registryFamilies.push(family);registryFamilies.sort();renderFamilyOptions()}const listed=devices.find(d=>d.device_id===current.device_id);if(listed)listed.registered=true;q('#status').textContent=`已保存 revision ${result.registry_revision}，${result.port_count} 个端口`;renderDevices()}catch(e){q('#status').textContent='保存失败：'+e.message}};init().catch(e=>q('#summary').textContent=e.message);
</script></body></html>"""


def _registrations(scan: DeviceScan, catalog: dict[str, object]) -> list[dict[str, object]]:
    registrations = []
    for template_id, raw in sorted(dict(catalog["templates"]).items()):
        record = dict(raw)
        # A relative filename is not an identity once arbitrary folders can be
        # selected at runtime.  Only byte-identical GDS files inherit a prior
        # registration; same-named but geometrically different files do not.
        if (
            record.get("sha256") != scan.source_sha256
            or record.get("top_cell") != scan.top_cell
        ):
            continue
        registrations.append(
            {
                "template_id": template_id,
                "family": record.get("family", "unclassified_device"),
                "ports": record.get("ports", {}),
                "registered_revision": record.get("registered_revision"),
            }
        )
    return registrations


def _summary(scan: DeviceScan, catalog: dict[str, object]) -> dict[str, object]:
    registrations = _registrations(scan, catalog)
    return {
        "device_id": scan.device_id,
        "relative_path": scan.relative_path,
        "top_cell": scan.top_cell,
        "source_layers": [list(layer) for layer in scan.source_layers],
        "candidate_count": len(scan.candidates),
        "registered": bool(registrations),
        "registered_template_ids": [item["template_id"] for item in registrations],
    }


def _device_payload(scan: DeviceScan, catalog: dict[str, object]) -> dict[str, object]:
    payload = scan.to_dict()
    payload["registrations"] = _registrations(scan, catalog)
    return payload


class OnboardingSession:
    """Mutable scan selection with an immutable project-local registry target."""

    def __init__(
        self,
        registry_folder: str | Path,
        source_folder: str | Path | None = None,
    ):
        self.registry = DeviceRegistry(registry_folder)
        self.registry.initialize(Path(registry_folder).name)
        self._lock = threading.Lock()
        self._source_folder: Path | None = None
        self._scans: tuple[DeviceScan, ...] = ()
        self._by_id: dict[str, DeviceScan] = {}
        if source_folder is not None:
            self.load_source(source_folder)

    def load_source(self, source_folder: str | Path) -> dict[str, object]:
        raw = str(source_folder).strip().strip('"')
        if not raw:
            raise SpecError("source_folder cannot be empty")
        source = Path(raw).expanduser().resolve()
        if not source.is_dir():
            raise SpecError(f"GDS source folder does not exist: {source}")
        # Complete the potentially expensive scan before swapping the active
        # session, so a failed folder never destroys the previous good state.
        scans = scan_gds_folder(source)
        by_id = {scan.device_id: scan for scan in scans}
        with self._lock:
            self._source_folder = source
            self._scans = scans
            self._by_id = by_id
        return self.state()

    def state(self) -> dict[str, object]:
        with self._lock:
            source = self._source_folder
            count = len(self._scans)
        catalog = load_registry_catalog(self.registry.catalog_path)
        return {
            "source_folder": str(source) if source is not None else None,
            "device_count": count,
            "registry_folder": str(self.registry.root),
            "registry_catalog": str(self.registry.catalog_path),
            "registered_template_count": len(dict(catalog["templates"])),
            "registry_revision": int(catalog.get("registry_revision", 0)),
        }

    def scans(self) -> tuple[DeviceScan, ...]:
        with self._lock:
            return self._scans

    def get_scan(self, device_id: str) -> DeviceScan:
        with self._lock:
            scan = self._by_id.get(device_id)
        if scan is None:
            raise SpecError(f"unknown device id {device_id!r}; reload the selected folder")
        return scan
