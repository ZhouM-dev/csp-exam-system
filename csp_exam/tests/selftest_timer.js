// Real shared script, with deterministic clocks and minimal DOM adapters.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const path = require('node:path');
let mono = 0, wall = 1700000000000, next = 0, active = new Map(), closed = false;
const ctx = {
  window: {}, Date: { now: () => wall }, performance: { now: () => mono },
  setInterval: fn => { active.set(++next, fn); return next; },
  clearInterval: id => active.delete(id),
  document: { readyState: 'loading', addEventListener() {},
    querySelector: () => ({ classList: { add: () => { closed = true; } } }) }
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../web/static/contest.js'), 'utf8'), ctx);
const bar = { getAttribute: () => null, classList: { toggle() {} }, querySelector: () => null };
const el = { textContent: '' };
ctx.window.initCountdown(el, bar, 1700000010, { now: 1700000000 });
assert.equal(el.textContent, '00:00:10');
wall += 86400000; mono = 1000;
[...active.values()].forEach(f => f());
assert.equal(el.textContent, '00:00:09');
mono = 9500; [...active.values()].forEach(f => f());
assert.equal(el.textContent, '00:00:01'); assert.equal(closed, false);
mono = 10000; [...active.values()].forEach(f => f());
assert.equal(closed, true);
ctx.window.initCountdown(el, bar, 1700000100, { now: 1700000010 });
assert.equal(el.textContent, '00:01:30'); assert.equal(active.size, 1);
console.log('Passed: client clock change, final fractional second, deadline, extension, single timer.');

async function windowTests() {
  function setup() {
    let clock = 0, payload = {phase:'pending',start_at:1700000010,end_at:1700000100,server_now:1700000000};
    const nodes = {'wait-countdown':{textContent:''}, 'wait-status':{textContent:''}};
    const timers = [], navigation = [], body = {cleared:false,replaceChildren(){this.cleared=true;}};
    let fail = false, calls = 0;
    const context = {window:{}, Date:{now:()=>9999999999999}, performance:{now:()=>clock},
      Number, AbortController, setTimeout:()=>1, clearTimeout(){},
      setInterval:(fn,ms)=>{timers.push({fn,ms});return timers.length;}, clearInterval(){},
      location:{replace:url=>navigation.push(url),reload:()=>navigation.push('reload')},
      document:{readyState:'loading',hidden:false,body,addEventListener(){},
        getElementById:id=>nodes[id]||null,querySelector:()=>null},
      fetch:async()=>{calls++;if(fail)throw new Error('offline');return {ok:true,json:async()=>({...payload})};}};
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../web/static/contest.js'),'utf8'),context);
    context.window.initContestWindow({...payload,url:'/api/contest-state?c=test',hall_url:'/hall?c=test'});
    return {nodes,navigation,body,get calls(){return calls;},
      time(ms){clock=ms;},state(next){payload={...payload,...next};},offline(value){fail=value;},
      async poll(){await timers.find(t=>t.ms===2000).fn();},
      async tick(){timers.find(t=>t.ms===200).fn();await Promise.resolve();await Promise.resolve();}};
  }
  const w=setup();await new Promise(setImmediate);
  assert.equal(w.nodes['wait-countdown'].textContent,'00:00:10');
  w.time(1000);await w.tick();assert.equal(w.nodes['wait-countdown'].textContent,'00:00:09');
  w.time(10000);w.state({server_now:1700000010});await w.poll();
  assert.equal(w.nodes['wait-countdown'].textContent,'00:00:00');
  assert.deepEqual(w.navigation,[]); // 客户端到零不能自行解锁。
  w.time(12000);w.state({phase:'running',server_now:1700000012});await w.poll();
  assert.deepEqual(w.navigation,['/hall?c=test']);assert.equal(w.body.cleared,true);
  await w.tick();await w.poll();assert.equal(w.navigation.length,1);

  const postponed=setup();await new Promise(setImmediate);
  postponed.time(2000);postponed.state({start_at:1700000050});await postponed.poll();
  assert.deepEqual(postponed.navigation,['reload']);

  const outage=setup();await new Promise(setImmediate);
  outage.time(10000);outage.offline(true);await outage.poll();
  assert.deepEqual(outage.navigation,[]);assert.match(outage.nodes['wait-status'].textContent,/自动重试/);
  outage.time(12000);outage.offline(false);outage.state({phase:'running',server_now:1700000012});await outage.poll();
  assert.deepEqual(outage.navigation,['/hall?c=test']);
  console.log('Passed: server start confirmation, incorrect client clock, reschedule, offline retry, single navigation.');
}
windowTests().catch(error=>{console.error(error);process.exitCode=1;});
