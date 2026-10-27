"""C静态样式演示构建：复用圆桌组件和已记录的stub局，不调用服务。"""
from pathlib import Path
import argparse
import hashlib
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from app.ui_helpers import project,read_events,LABELS
from app.ui_metrics import read_calls,summarize_calls
from app.ui_presenter import EVENT_NAMES,describe_public
from app.ui_scene import scene_model


def safe_json(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')


def build(game_id='b4e7eaa6a071',output=None):
    events=read_events(ROOT/'runs',game_id)
    if not any(e.get('source')=='stub' for e in events) or any('deepseek' in str(e.get('source','')).lower() for e in events):
        raise ValueError('Only an explicitly offline stub run can be used for this public style preview.')
    calls=read_calls(ROOT/'runs',game_id) or []
    summary_path=ROOT/'runs'/game_id/'summary.json'
    summary=json.loads(summary_path.read_text(encoding='utf-8')) if summary_path.exists() else None
    classification_at={e['source_event_id']:e['event_id'] for e in events
                       if e['type'] in {'utterance_classification','utterance_classification_failed'}}
    call_visible_at=[classification_at.get(c.get('event_id'),len(events)+1)
                     if c.get('caller_type')=='small_model' else c.get('event_id',len(events)+1) for c in calls]
    records={'events':events,'calls':calls,'call_visible_at':call_visible_at,'summary':summary,
             'names':EVENT_NAMES,'labels':LABELS,
             'descriptions':{e['event_id']:describe_public(e) for e in events}}
    snapshots=[]
    for cursor in range(len(events)+1):
        prefix=events[:cursor];state=project(prefix)
        value=scene_model(prefix,status=state['status'],game_id=game_id,replay=True)
        value['source']='离线联调'
        value['analysis']['evidence_note']='演示为已保存的离线记录；此页面不执行模型请求。'
        if value['current']['kind']=='game_created':value['current']['body']='身份已分配。点击播放或下一步，观察机器人行动。'
        visible_calls=[c for c,visible in zip(calls,call_visible_at) if visible<=cursor or cursor==len(events)]
        value['inspection_stats']=summarize_calls(visible_calls,summary if cursor==len(events) and summary else {})
        value['metrics']={'call_count':len(visible_calls)}
        snapshots.append(value)
    initial=next((i for i,m in enumerate(snapshots) if m['analysis']['classified']),0)
    original_scene=(ROOT/'app/scene/index.html').read_text(encoding='utf-8-sig')
    template='''<!doctype html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="狼境AI圆桌可视化样式演示；离线记录回放，不调用模型。">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-src 'self' about:; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>狼境 · 在线样式演示</title><style>
*{box-sizing:border-box}body{margin:0;background:#f2f4e9;color:#314737;font-family:"Segoe UI","Microsoft YaHei",sans-serif;transition:background .7s,color .7s}
main{max-width:1480px;margin:0 auto;padding:22px 26px}header{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:12px}h1{font-size:26px;letter-spacing:3px;margin:0}h1 small{font-size:10px;font-weight:400;letter-spacing:2px;opacity:.5;margin-left:12px}.badge{border:1px solid #809b7955;border-radius:20px;padding:7px 13px;font-size:12px}.intro{font-size:12px;line-height:1.8;opacity:.8;margin:0 0 14px}.controls{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:12px}button,select{font:inherit;font-size:12px;border:1px solid #809b7970;border-radius:10px;padding:10px 15px;background:#fffef9;color:#365c45;cursor:pointer}button.primary{background:#326a50;color:white;border-color:#326a50}button:disabled{opacity:.4;cursor:default}button:focus-visible,select:focus-visible{outline:3px solid #a4bea9;outline-offset:2px}.timeline{display:flex;align-items:center;gap:12px;margin:0 0 14px;font-size:12px}.timeline input{flex:1;min-width:100px;accent-color:#538668}#position{min-width:90px;text-align:right}iframe{width:100%;height:650px;border:0;display:block;border-radius:28px}.foot{margin-top:12px;font-size:11px;line-height:1.9;opacity:.65}details{border-top:1px solid #809b7933;margin-top:12px;padding-top:10px;font-size:12px;line-height:1.9}summary{cursor:pointer}.daylinks{margin-left:auto;display:flex;gap:8px}.night{background:#101e2e;color:#e1eaf0}.night button:not(.primary),.night select{background:#1c3042;color:#d9e8e4;border-color:#54716b}label{font-size:12px}@media(max-width:800px){main{padding:16px 10px}header{align-items:flex-start}h1{font-size:21px}h1 small{display:none}.badge{font-size:10px}button,select{padding:9px 11px}.daylinks{margin-left:0}iframe{height:790px}}
__INSPECT_CSS__
</style></head><body><main>
<header><h1>狼境 <small>AI ROUND TABLE</small></h1><span class="badge">在线样式演示 · 离线记录回放</span></header>
<p class="intro">先看看六位机器人如何行动、发言和出局。这里展示已保存的离线对局，分类来自 A 的本地模型；不会调用 DeepSeek，也不会产生 API 费用。</p>
<div class="controls"><button class="primary" id="play">播放演示</button><button id="restart">从发牌开始</button><button id="previous">上一步</button><button id="next">下一步</button><label>速度 <select id="speed" aria-label="播放速度"><option value="2400">慢速</option><option value="1600" selected>正常</option><option value="800">快速</option></select></label><div class="daylinks"><button id="night">看夜间行动</button><button id="speech">看发言与分类</button><button id="result">看投票结果</button></div></div>
<div class="timeline"><span id="activity" role="status" aria-live="polite">已暂停，可自由查看</span><input id="cursor" type="range" min="0" max="__MAX__" value="__INITIAL__" aria-label="演示进度"><span id="position"></span></div>
<iframe id="scene" title="六机器人圆桌与同步分类演示" sandbox="allow-scripts"></iframe>
<details id="inspection" class="inspection"><summary>检查记录 · 完整事件 / 投票原件 / 调用统计</summary><div class="inspect-inner"><div id="inspect-meta" class="inspect-meta"></div><div id="inspect-metrics" class="inspect-metrics"></div><div id="inspect-players" class="inspect-players"></div><div class="inspect-tabs" role="tablist" aria-label="检查记录分类"><button id="inspect-tab-public" role="tab" data-inspect-tab="public" aria-controls="inspect-panel" aria-selected="true">公开事件与分类</button><button id="inspect-tab-votes" role="tab" data-inspect-tab="votes" aria-controls="inspect-panel" aria-selected="false">投票与结果</button><button id="inspect-tab-private" role="tab" data-inspect-tab="private" aria-controls="inspect-panel" aria-selected="false">仅上帝视角</button><button id="inspect-tab-stats" role="tab" data-inspect-tab="stats" aria-controls="inspect-panel" aria-selected="false">统计与原始事件</button></div><div id="inspect-panel" class="inspect-panel" role="tabpanel" aria-labelledby="inspect-tab-public"></div></div></details>
<div class="foot">这是 C 的界面初版演示，供成员提出布局、可读性和操作建议。线上演示包含圆桌、分类、回放和下方只读检查区；真实建局与模型调用仍在本机程序中使用。</div>
<details><summary>这是什么版本，应该反馈什么？</summary><p>来源对局：__GAME__；离线玩家（stub）＋A 实际分类结果。页面中的身份、发言、死亡和胜负来自同一份已保存记录，不代表真实 DeepSeek 实验。</p><p>请重点反馈：能否看懂谁在行动、发言是否清楚、昼夜和出局是否明显、小模型区域是否易懂、播放和暂停是否顺手。</p><p>这不是正式实验结果，不用于宣称模型准确率、真实调用费用或规则全部验收通过。</p></details>
</main><script id="inspection-records" type="application/json">__RECORDS__</script><script id="snapshots" type="application/json">__SNAPSHOTS__</script><script id="scene-source" type="application/json">__SCENE__</script>
<script>
const snapshots=JSON.parse(document.getElementById('snapshots').textContent),frame=document.getElementById('scene'),slider=document.getElementById('cursor'),play=document.getElementById('play');
let cursor=__INITIAL__,timer=null,childReady=false;
function draw(){const m=snapshots[cursor];document.body.classList.toggle('night',m.night);slider.value=cursor;document.getElementById('position').textContent=cursor+' / '+(snapshots.length-1)+' 步';document.getElementById('previous').disabled=cursor===0;document.getElementById('next').disabled=cursor===snapshots.length-1;play.textContent=timer?'暂停演示':'播放演示';document.getElementById('activity').textContent=timer?'正在播放离线记录':cursor===snapshots.length-1?'演示结束，可从头播放':'已暂停，可自由查看';if(childReady)frame.contentWindow.postMessage({type:'streamlit:render',args:{model:m}},'*');renderInspection()}
function pause(){if(timer)clearInterval(timer);timer=null;draw()}
function start(){if(cursor>=snapshots.length-1)cursor=0;timer=setInterval(()=>{if(cursor<snapshots.length-1)cursor++;if(cursor>=snapshots.length-1)pause();else draw()},Number(document.getElementById('speed').value));draw()}
function jump(index){pause();cursor=index;draw()}
window.addEventListener('message',event=>{if(event.source!==frame.contentWindow||!event.data)return;if(event.data.type==='streamlit:componentReady'){childReady=true;draw()}if(event.data.type==='streamlit:setFrameHeight'){const height=Number(event.data.height);if(height>=600&&height<=1100)frame.style.height=height+'px'}});
__INSPECT_JS__
frame.srcdoc=JSON.parse(document.getElementById('scene-source').textContent);
play.addEventListener('click',()=>timer?pause():start());document.getElementById('restart').addEventListener('click',()=>{pause();cursor=0;start()});document.getElementById('previous').addEventListener('click',()=>jump(Math.max(0,cursor-1)));document.getElementById('next').addEventListener('click',()=>jump(Math.min(snapshots.length-1,cursor+1)));slider.addEventListener('input',()=>jump(Number(slider.value)));document.getElementById('speed').addEventListener('change',()=>{if(timer){pause();start()}});
function find(kind){const index=snapshots.findIndex(m=>m.current.kind===kind);return index<0?0:index}
document.getElementById('night').addEventListener('click',()=>jump(find('wolf_proposal')));document.getElementById('speech').addEventListener('click',()=>jump(__INITIAL__));document.getElementById('result').addEventListener('click',()=>jump(find('vote_tally')));document.addEventListener('visibilitychange',()=>{if(document.hidden)pause()});draw();
</script></body></html>'''
    html=(template.replace('__MAX__',str(len(events))).replace('__INITIAL__',str(initial))
          .replace('__GAME__',game_id).replace('__INSPECT_CSS__',(ROOT/'app/scene/preview_details.css').read_text(encoding='utf-8-sig'))
          .replace('__INSPECT_JS__',(ROOT/'app/scene/preview_details.js').read_text(encoding='utf-8-sig'))
          .replace('__RECORDS__',safe_json(records)).replace('__SNAPSHOTS__',safe_json(snapshots)).replace('__SCENE__',safe_json(original_scene)))
    destination=Path(output) if output else ROOT/'preview'
    destination.mkdir(parents=True,exist_ok=True)
    (destination/'index.html').write_text(html,encoding='utf-8')
    (destination/'.nojekyll').write_text('',encoding='utf-8')
    manifest={'version':'c-preview-v2-with-inspection','inspection_tabs':4,'calls':len(calls),'game_id':game_id,'events':len(events),'source':'stub players and recorded local A classification',
              'mode':'read-only static replay','deepseek_calls':0,'initial_cursor':initial,
              'scene_sha256':hashlib.sha256(original_scene.encode()).hexdigest(),
              'html_sha256':hashlib.sha256(html.encode()).hexdigest()}
    (destination/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--game-id',default='b4e7eaa6a071');parser.add_argument('--output')
    args=parser.parse_args();print(json.dumps(build(args.game_id,args.output),ensure_ascii=False))
