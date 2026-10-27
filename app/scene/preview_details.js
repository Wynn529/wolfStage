const inspection=document.getElementById('inspection'), records=JSON.parse(document.getElementById('inspection-records').textContent);
let inspectionTab='public';
const inspectEscape=value=>String(value==null?'':value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const inspectJson=(value,label='查看原始字段')=>'<details><summary>'+inspectEscape(label)+'</summary><pre>'+inspectEscape(JSON.stringify(value,null,2))+'</pre></details>';
const recordTitle=e=>'#'+e.event_id+' · day='+e.day+' · '+(records.names[e.type]||e.type);
const roleNames={wolf:'狼人',seer:'预言家',villager:'平民'};
const stateNames={unknown:'未开始',running:'回放进行中',paused:'记录中已暂停',finished:'对局已结束',failed:'记录中失败',incomplete:'达到上限，未完成'};
function inspectTable(headers,rows){return '<div class="table-scroll"><table class="inspect-table"><thead><tr>'+headers.map(h=>'<th>'+inspectEscape(h)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(row=>'<tr>'+row.map(v=>'<td>'+inspectEscape(v)+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>'}
function eventCard(e,body){return '<article class="record-card"><div class="record-tag">'+inspectEscape(recordTitle(e))+'</div>'+body+'</article>'}
function publicInspection(events){
 const labels=new Map(events.filter(e=>e.type==='utterance_classification').map(e=>[e.source_event_id,e.result]));
 const items=events.filter(e=>e.visibility==='public'&&e.type!=='utterance_classification');
 if(!items.length)return '<p class="inspect-empty">当前回放位置尚无公开事件。</p>';
 return items.map(e=>{let body='';if(e.type==='public_speech'){body='<p><b>'+inspectEscape(e.actor)+'号：</b>'+inspectEscape(e.text||'')+'</p>';const label=labels.get(e.event_id);body+=label?'<p><span class="class-label">'+inspectEscape(records.labels[label.intent]||label.intent)+'</span>模型 '+inspectEscape(label.model_version)+' · 分数未校准，不是概率</p>'+inspectJson(label.extracted_entities||{},'查看提取信息'):'<p class="inspect-meta">当前回放位置尚无对应分类。</p>'}else{body='<p>'+inspectEscape(records.descriptions[e.event_id]||records.names[e.type]||e.type)+'</p>';if(e.type==='vote_tally'&&e.counts)body+=inspectTable(['目标','票数（B记录）'],Object.entries(e.counts).map(([target,count])=>[target+'号',count]))}return eventCard(e,body+inspectJson(e))}).join('')
}
function voteInspection(events){
 const groups=[];let current=null;
 for(const e of events){if(e.visibility!=='public'||!['vote','vote_tally'].includes(e.type))continue;if(!current||current.day!==e.day){current={day:e.day,votes:[],tally:null};groups.push(current)}if(e.type==='vote')current.votes.push(e);else{current.tally=e;current=null}}
 if(!groups.length)return '<p class="inspect-empty">当前回放位置尚无公开投票记录。</p>';
 let html='<p class="inspect-meta">沿用B记录的逐票、有效性和计票；演示页面不重新裁决。</p>';
 html+=groups.map((group,i)=>{let body='<p><b>投票记录 '+(i+1)+' · day='+group.day+'</b></p>';body+=group.votes.length?inspectTable(['事件','投票人','目标','有效性'],group.votes.map(e=>[e.event_id,e.actor+'号',e.target==null?'未提供':e.target+'号',e.valid===true?'有效（B标记）':e.valid===false?'无效/弃权（B未细分）':'未提供'])):'<p>当前事件没有这组逐票记录。</p>';if(group.tally){const tally=group.tally;body+='<p>'+inspectEscape(records.descriptions[tally.event_id]||'计票记录')+'</p>';body+=tally.counts?inspectTable(['目标','票数（B记录）'],Object.entries(tally.counts).map(([target,count])=>[target+'号',count])):'<p>票数未提供。</p>';body+=inspectJson(tally,'查看计票原件')}else body+='<p>当前回放位置尚未收到这组计票结果。</p>';return '<article class="record-card">'+body+'</article>'}).join('');
 html+=events.filter(e=>e.visibility==='public'&&['exile','game_over','game_limit'].includes(e.type)).map(e=>eventCard(e,'<p>'+inspectEscape(records.descriptions[e.event_id])+'</p>')).join('');return html
}
function privateInspection(events){const items=events.filter(e=>e.visibility!=='public'||e.reason);return '<div class="inspect-notice">只供观察者查看。这里是已保存的离线记录，不会回传给玩家或模型。reason表示角色自述依据。</div>'+(items.length?items.map(e=>eventCard(e,(e.reason?'<p>角色自述依据：'+inspectEscape(e.reason)+'</p>':'')+inspectJson(e,'查看私密事件记录'))).join(''):'<p class="inspect-empty">当前回放位置没有私密记录。</p>')}
function statsInspection(events,m){
 const check=m.inspection_stats,calls=records.calls.filter((call,i)=>cursor===snapshots.length-1||records.call_visible_at[i]<=cursor);
 let html='<div class="inspect-notice">仅统计当前回放位置可关联的调用记录；费用未计算。离线玩家和本地小模型的API token记为不适用，不把次数当成准确率。</div>';
 html+=inspectTable(['调用方','尝试','首次','重试','成功','失败','兜底'],check.rows.map(row=>[({player:'玩家',host:'主持',small_model:'小模型',reviewer:'评审'})[row.caller_type]||row.caller_type,row.attempts,row.first_attempts,row.retries,row.success,row.failed,row.fallback]));
 for(const [key,info] of Object.entries(check.usage))html+='<p class="inspect-meta">'+inspectEscape(key)+'：'+inspectEscape(info.display)+'；缺失 '+info.missing+' 次，不适用 '+info.not_applicable+' 次。</p>';
 for(const difference of check.differences)html+='<div class="inspect-notice inspect-warning">统计待核对：'+inspectEscape(difference)+'</div>';
 html+=cursor===snapshots.length-1&&records.summary?inspectJson(records.summary,'查看B整局汇总原件'):'<p class="inspect-meta">尚未展示整局汇总；回放到结局后可查看，避免提前显示未来结果。</p>';
 html+='<button id="download-displayed" class="inspect-download">下载当前已显示事件（查询副本）</button>';
 html+=inspectJson(events,'查看当前已显示事件 JSON')+inspectJson(calls,'查看当前可关联调用记录 JSON');return html
}
function renderInspection(){
 if(!inspection.open)return;
 const m=snapshots[cursor],events=records.events.filter(e=>e.event_id<=cursor);
 document.getElementById('inspect-meta').textContent='对局 '+m.game_id+' · 离线玩家＋A本地模型 · 当前事件 '+cursor+' / '+(snapshots.length-1);
 document.getElementById('inspect-metrics').innerHTML=[['状态',stateNames[m.status]||m.status],['阶段',({night:'夜间',day_speak:'白天发言',day_vote:'白天投票'})[m.phase]||m.phase],['存活人数',cursor?m.players.filter(p=>p.alive).length:'尚未发牌'],['最后显示事件',cursor]].map(([label,value])=>'<div class="inspect-metric">'+inspectEscape(label)+'<strong>'+inspectEscape(value)+'</strong></div>').join('');
 document.getElementById('inspect-players').innerHTML=m.players.map(p=>'<div class="inspect-player '+(!p.alive?'dead':'')+'">'+p.id+'号 · '+inspectEscape(p.role_name)+'<br>'+(!cursor?'等待发牌':p.alive?'存活':'已出局')+'</div>').join('');
 document.querySelectorAll('[data-inspect-tab]').forEach(button=>{button.setAttribute('aria-selected',String(button.dataset.inspectTab===inspectionTab));button.tabIndex=button.dataset.inspectTab===inspectionTab?0:-1});
 const panel=document.getElementById('inspect-panel');panel.setAttribute('aria-labelledby','inspect-tab-'+inspectionTab);
 panel.innerHTML=inspectionTab==='public'?publicInspection(events):inspectionTab==='votes'?voteInspection(events):inspectionTab==='private'?privateInspection(events):statsInspection(events,m);
 const download=document.getElementById('download-displayed');if(download)download.onclick=()=>{const blob=new Blob([JSON.stringify(events,null,2)],{type:'application/json;charset=utf-8'});const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='displayed_events_'+m.game_id+'_'+cursor+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};
}
inspection.addEventListener('toggle',renderInspection);
const inspectTabs=[...document.querySelectorAll('[data-inspect-tab]')];inspectTabs.forEach((button,i)=>{button.addEventListener('click',()=>{inspectionTab=button.dataset.inspectTab;renderInspection()});button.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight'].includes(event.key))return;event.preventDefault();const next=(i+(event.key==='ArrowRight'?1:-1)+inspectTabs.length)%inspectTabs.length;inspectionTab=inspectTabs[next].dataset.inspectTab;renderInspection();inspectTabs[next].focus()})});
