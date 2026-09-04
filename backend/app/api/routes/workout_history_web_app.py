"""Telegram Mini App for visual, read-only workout history."""
# ruff: noqa: E501

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from app.api.routes.telegram_web_app import (
    history_session_identity,
    telegram_web_app_identity,
)
from app.schemas.workout_history import WorkoutHistoryWebAppRequest
from app.services.workout_history import (
    WorkoutHistoryCursorError,
    WorkoutHistoryService,
    WorkoutHistoryUserNotFoundError,
)

router = APIRouter(tags=["telegram-web-app"])

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{color-scheme:light dark}*{box-sizing:border-box}body{font:15px system-ui,-apple-system,sans-serif;margin:0;padding:16px;background:var(--tg-theme-secondary-bg-color,#f4f4f5);color:var(--tg-theme-text-color,#111)}main{max-width:700px;margin:auto}h1{font-size:25px;margin:4px 0}h2{font-size:17px;margin:0 0 10px}.muted{color:var(--tg-theme-hint-color,#666);margin:6px 0 18px}.panel,.card{background:var(--tg-theme-bg-color,#fff);border-radius:14px;padding:14px;margin:12px 0}.controls,.chips,.toggle{display:flex;flex-wrap:wrap;gap:8px}.chip,button{border:0;border-radius:9px;padding:9px 11px;font:inherit;background:var(--tg-theme-secondary-bg-color,#e5e7eb);color:var(--tg-theme-text-color,#111)}.chip.active,.toggle button.active{background:var(--tg-theme-button-color,#2481cc);color:var(--tg-theme-button-text-color,#fff)}.custom{display:flex;gap:8px;align-items:end;margin-top:10px}.custom label{font-size:12px;flex:1}.custom input{display:block;width:100%;margin-top:4px;padding:8px;border:1px solid var(--tg-theme-hint-color,#aaa);border-radius:8px;background:var(--tg-theme-bg-color,#fff);color:inherit}.custom button{background:var(--tg-theme-button-color,#2481cc);color:var(--tg-theme-button-text-color,#fff)}.totals{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.total{padding:11px;border-radius:10px;background:var(--tg-theme-secondary-bg-color,#f4f4f5)}.total b{display:block;font-size:20px}.chart{height:210px;display:flex;align-items:end;gap:3px;border-bottom:1px solid var(--tg-theme-hint-color,#aaa);padding-top:14px;overflow-x:auto}.bar-group{height:180px;min-width:24px;display:flex;flex-direction:column-reverse;justify-content:flex-start}.bar{width:100%;min-height:0}.bar-label{font-size:9px;white-space:nowrap;transform:rotate(-45deg);transform-origin:left top;margin-top:8px}.legend{display:flex;gap:9px;flex-wrap:wrap;font-size:12px;margin-top:12px}.legend i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:3px}.card-head{display:flex;justify-content:space-between;gap:8px}.sport{font-weight:700}.metrics{display:flex;flex-wrap:wrap;gap:7px;margin-top:9px;color:var(--tg-theme-hint-color,#666)}.metric{background:var(--tg-theme-secondary-bg-color,#f4f4f5);padding:4px 7px;border-radius:7px}.hidden{display:none}.empty,.error{padding:14px;text-align:center;color:var(--tg-theme-hint-color,#666)}.error{color:#b91c1c}#load-more{width:100%;background:var(--tg-theme-button-color,#2481cc);color:var(--tg-theme-button-text-color,#fff);margin:12px 0 28px}@media(max-width:390px){.totals{grid-template-columns:1fr}.custom{flex-wrap:wrap}.custom button{width:100%}}
</style></head><body><main><h1>Workout history</h1><p class="muted">Review your completed training and refine your plan with every workout.</p><div class="panel"><div class="controls" id="ranges"><button class="chip" data-days="7">7 days</button><button class="chip active" data-days="30">30 days</button><button class="chip" data-days="90">90 days</button><button class="chip" data-days="custom">Custom</button></div><div class="custom hidden" id="custom"><label>From<input id="start" type="date"></label><label>To<input id="end" type="date"></label><button id="apply">Apply</button></div><div class="chips" id="sports" style="margin-top:12px"></div></div><p id="error" class="error hidden"></p><section class="panel"><div class="totals"><div class="total"><span>Sessions</span><b id="sessions">-</b></div><div class="total"><span>Training time</span><b id="duration">-</b></div><div class="total"><span>Distance</span><b id="distance">-</b></div></div></section><section class="panel"><div class="card-head"><h2>Training volume</h2><div class="toggle"><button class="active" data-metric="time">Time</button><button data-metric="distance">Distance</button></div></div><div id="chart" class="chart"></div><div id="legend" class="legend"></div></section><section><h2>Workouts</h2><div id="workouts"></div><div id="empty" class="panel empty hidden">No completed workouts match these filters yet. Send a workout screenshot to add one.</div><button id="load-more" class="hidden">Load more</button></section></main>
<script>
const webApp=window.Telegram&&window.Telegram.WebApp;webApp&&webApp.ready();const historySession=new URLSearchParams(location.search).get('session');
const colors={RUNNING:'#ef4444',CYCLING:'#3b82f6',SWIMMING:'#06b6d4',STRENGTH:'#8b5cf6',HIKING:'#f59e0b',OTHER:'#64748b'};
const state={days:30,discipline:null,metric:'time',cursor:null,data:null};
const $=s=>document.querySelector(s);const dateISO=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;const formatDuration=s=>{const h=Math.floor(s/3600),m=Math.round((s%3600)/60);return h?`${h}h ${m}m`:`${m} min`};const formatDistance=m=>m>=1000?`${(m/1000).toFixed(m>=10000?0:1)} km`:`${Math.round(m)} m`;const sport=s=>s.charAt(0)+s.slice(1).toLowerCase();
function currentRange(){const end=new Date(),start=new Date();if(state.days!=='custom')start.setDate(end.getDate()-Number(state.days)+1);else return{start_date:$('#start').value,end_date:$('#end').value};return{start_date:dateISO(start),end_date:dateISO(end)}}
function renderSports(){const available=new Set(state.data?.available_disciplines||[]);if(state.discipline)available.add(state.discipline);const options=[null,...Object.keys(colors).filter(x=>available.has(x))];$('#sports').innerHTML=options.map(value=>`<button class="chip ${state.discipline===value?'active':''}" data-sport="${value||''}">${value?sport(value):'All sports'}</button>`).join('');document.querySelectorAll('[data-sport]').forEach(b=>b.onclick=()=>{state.discipline=b.dataset.sport||null;state.cursor=null;load(false)});}
function renderChart(){const buckets=state.data.chart_buckets;const field=state.metric==='time'?'duration_seconds_by_discipline':'distance_meters_by_discipline';const maximum=Math.max(1,...buckets.map(b=>Object.values(b[field]).reduce((a,v)=>a+v,0)));$('#chart').innerHTML=buckets.map(b=>{const values=b[field];return `<div class="bar-group" title="${b.label}">${Object.entries(values).map(([d,v])=>`<div class="bar" style="height:${v/maximum*100}%;background:${colors[d]||colors.OTHER}"></div>`).join('')}<span class="bar-label">${b.label}</span></div>`}).join('');const disciplines=[...new Set(buckets.flatMap(b=>Object.keys(b[field])))];$('#legend').innerHTML=disciplines.map(d=>`<span><i style="background:${colors[d]||colors.OTHER}"></i>${sport(d)}</span>`).join('')||'<span class="muted">No volume recorded for this view.</span>';}
function renderWorkouts(items,append){const container=$('#workouts');if(!append)container.innerHTML='';items.forEach(item=>{const metrics=[`<span class="metric">${formatDuration(item.duration_seconds)}</span>`];if(item.distance_meters!==null)metrics.push(`<span class="metric">${formatDistance(item.distance_meters)}</span>`);if(item.calories_kcal!==null)metrics.push(`<span class="metric">${Math.round(item.calories_kcal)} kcal</span>`);if(item.average_heart_rate!==null)metrics.push(`<span class="metric">${Math.round(item.average_heart_rate)} bpm avg</span>`);const started=new Date(item.started_at),zone=state.data.timezone;container.insertAdjacentHTML('beforeend',`<article class="card"><div class="card-head"><div><div class="sport">${sport(item.discipline)}</div>${item.title?`<div class="muted">${item.title}</div>`:''}</div><time>${started.toLocaleDateString(undefined,{day:'numeric',month:'short',year:'numeric',timeZone:zone})}<br>${started.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',timeZone:zone})}</time></div><div class="metrics">${metrics.join('')}</div></article>`)});$('#empty').classList.toggle('hidden',container.children.length>0);}
function render(data,append){state.data=data;$('#sessions').textContent=data.totals.session_count;$('#duration').textContent=formatDuration(data.totals.duration_seconds);$('#distance').textContent=data.totals.distance_meters?formatDistance(data.totals.distance_meters):'-';renderSports();renderChart();renderWorkouts(data.workouts,append);state.cursor=data.next_cursor;$('#load-more').classList.toggle('hidden',!state.cursor);}
async function load(append){$('#error').classList.add('hidden');const range=currentRange();if(!range.start_date||!range.end_date||range.start_date>range.end_date){$('#error').textContent='Choose a valid start and end date.';$('#error').classList.remove('hidden');return}if(!webApp?.initData&&!historySession){$('#error').textContent='Open Workout history from Telegram.';$('#error').classList.remove('hidden');return}const payload={init_data:webApp?.initData||null,session_token:historySession,...range,discipline:state.discipline,cursor:append?state.cursor:null};try{const response=await fetch('/webapp/workout-history/data',{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});if(!response.ok)throw new Error();render(await response.json(),append)}catch(_){$('#error').textContent='Unable to load your workout history. Return to the coach and open it again.';$('#error').classList.remove('hidden')}}
document.querySelectorAll('[data-days]').forEach(button=>button.onclick=()=>{state.days=button.dataset.days==='custom'?'custom':Number(button.dataset.days);state.cursor=null;document.querySelectorAll('[data-days]').forEach(x=>x.classList.toggle('active',x===button));$('#custom').classList.toggle('hidden',state.days!=='custom');if(state.days!=='custom')load(false)});document.querySelectorAll('[data-metric]').forEach(button=>button.onclick=()=>{state.metric=button.dataset.metric;document.querySelectorAll('[data-metric]').forEach(x=>x.classList.toggle('active',x===button));renderChart()});$('#apply').onclick=()=>{state.cursor=null;load(false)};$('#load-more').onclick=()=>load(true);const today=dateISO(new Date());$('#end').value=today;const month=new Date();month.setDate(month.getDate()-29);$('#start').value=dateISO(month);load(false);
</script></body></html>"""

# Keep the Mini App self-contained while giving its compact chart enough context
# to be understandable on a phone and usable with keyboard/screen-reader input.
_PAGE = (
    _PAGE.replace(
        "</style>",
        """.chart-meta{font-size:12px;margin:2px 0 10px;color:var(--tg-theme-hint-color,#666)}.chart-detail{min-height:20px;font-size:12px;margin:8px 0 0;color:var(--tg-theme-hint-color,#666)}.bar-group{border:0;border-radius:0;padding:0;background:transparent;color:inherit;cursor:pointer;text-align:left}.bar-group:focus-visible{outline:2px solid var(--tg-theme-button-color,#2481cc);outline-offset:2px}.chart-empty{align-self:center;width:100%;text-align:center;color:var(--tg-theme-hint-color,#666)}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}#loading{margin:0 0 8px}</style>""",
    )
    .replace(
        '<div class="card-head"><h2>Training volume</h2>',
        '<div class="card-head"><div><h2>Training volume</h2><p id="chart-context" class="chart-meta">Daily totals · Time</p></div>',
    )
    .replace(
        '<div id="chart" class="chart"></div><div id="legend" class="legend"></div>',
        '<div id="chart" class="chart" role="img" aria-describedby="chart-summary chart-detail"></div><p id="chart-detail" class="chart-detail">Select a bar for its total.</p><p id="chart-summary" class="sr-only"></p><div id="legend" class="legend"></div>',
    )
    .replace(
        '<p id="error" class="error hidden"></p>',
        '<p id="loading" class="muted">Loading workout history…</p><p id="error" class="error hidden"></p>',
    )
    .replace(
        "function renderWorkouts(items,append){",
        """function renderChart(){const buckets=state.data.chart_buckets;const field=state.metric==='time'?'duration_seconds_by_discipline':'distance_meters_by_discipline';const unit=state.metric==='time'?'training time':'distance';const bucketKind=buckets.length>31?'Weekly':'Daily';const fmt=v=>state.metric==='time'?formatDuration(v):formatDistance(v);const total=b=>Object.values(b[field]).reduce((a,v)=>a+v,0);const maximum=Math.max(1,...buckets.map(total));$('#chart-context').textContent=`${bucketKind} totals · ${unit}`;if(!buckets.some(total)){$('#chart').innerHTML='<p class="chart-empty">No '+unit+' recorded for this view.</p>';$('#chart').setAttribute('aria-label','No '+unit+' recorded for this view.');$('#chart-summary').textContent='No '+unit+' recorded for this view.';$('#legend').innerHTML='';return}const descriptions=[];$('#chart').innerHTML=buckets.map((b,index)=>{const value=total(b),parts=Object.entries(b[field]).map(([d,v])=>`${sport(d)} ${fmt(v)}`);const description=`${b.label}: ${fmt(value)}${parts.length?` (${parts.join(', ')})`:''}`;descriptions.push(description);const label=index===0||index===buckets.length-1||index%Math.ceil(buckets.length/6)===0?b.label:'';return `<button class="bar-group" type="button" aria-label="${description}" title="${description}" data-chart-detail="${description.replace(/&/g,'&amp;').replace(/\"/g,'&quot;')}">${Object.entries(b[field]).map(([d,v])=>`<span class="bar" style="height:${v/maximum*100}%;background:${colors[d]||colors.OTHER}"></span>`).join('')}<span class="bar-label">${label}</span></button>`}).join('');$('#chart').setAttribute('aria-label',`${bucketKind} ${unit} chart`);$('#chart-summary').textContent=descriptions.join('. ');document.querySelectorAll('[data-chart-detail]').forEach(bar=>{const show=()=>$('#chart-detail').textContent=bar.dataset.chartDetail;bar.onmouseenter=show;bar.onfocus=show;bar.onclick=show});const disciplines=[...new Set(buckets.flatMap(b=>Object.keys(b[field])))];$('#legend').innerHTML=disciplines.map(d=>`<span><i style="background:${colors[d]||colors.OTHER}"></i>${sport(d)}</span>`).join('');}
function renderWorkouts(items,append){""",
    )
    .replace(
        "async function load(append){$('#error').classList.add('hidden');",
        "async function load(append){$('#error').classList.add('hidden');$('#loading').classList.remove('hidden');",
    )
    .replace(
        "$('#error').classList.remove('hidden')}}",
        "$('#error').classList.remove('hidden')}finally{$('#loading').classList.add('hidden')}}",
    )
)


@router.get("/webapp/workout-history", response_class=HTMLResponse)
async def workout_history_web_app() -> HTMLResponse:
    return HTMLResponse(_PAGE, headers={"Cache-Control": "no-store"})


@router.post("/webapp/workout-history/data")
async def workout_history_data(request: Request) -> dict[str, object]:
    try:
        body = WorkoutHistoryWebAppRequest.model_validate(await request.json())
    except ValidationError as error:
        raise HTTPException(status_code=422, detail="invalid history query") from error
    if body.init_data:
        identity = telegram_web_app_identity(
            settings=request.app.state.settings,
            init_data=body.init_data,
        )
    elif body.session_token:
        identity = history_session_identity(
            settings=request.app.state.settings,
            session_token=body.session_token,
        )
    else:
        raise HTTPException(status_code=401, detail="missing Telegram session")
    try:
        result = await WorkoutHistoryService(request.app.state.session_factory).query(
            identity=identity, request=body
        )
    except WorkoutHistoryUserNotFoundError as error:
        raise HTTPException(status_code=404, detail="athlete not found") from error
    except WorkoutHistoryCursorError as error:
        raise HTTPException(status_code=422, detail="invalid history cursor") from error
    return result.model_dump(mode="json")
