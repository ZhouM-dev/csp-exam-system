// SPDX-License-Identifier: AGPL-3.0-or-later
// Transport and data adapter only. Upstream HydroJudge performs ALL judging.
'use strict';
require('@hydrooj/register');
const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const { JudgeTask } = require('@hydrooj/hydrojudge/src/task');
const version = require('@hydrooj/hydrojudge/package.json').version;
const ROOT = process.env.CSP_EXAM_DATA_DIR || '/var/lib/csp-exam';
const WORK = path.join(ROOT, 'hydro-work');
const TOKEN = process.env.CSP_HYDRO_TOKEN;
if (!TOKEN || TOKEN.length < 32) throw new Error('A private bridge token is required');
let pending = Promise.resolve();

async function judge(data) {
  if (!/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(data.pid) ||
      !/^[a-z0-9_]{1,32}$/.test(data.code) || !['.cpp','.cc','.cxx','.c++','.c'].includes(data.ext) ||
      !['file', 'stdio'].includes(data.io_mode) ||
      !Number.isInteger(data.time_ms) || data.time_ms < 1 || data.time_ms > 480000 ||
      !Number.isInteger(data.memory_mb) || data.memory_mb < 1 || data.memory_mb > 1024 ||
      !Array.isArray(data.cases) || !data.cases.length || data.cases.length > 1024) {
    throw new Error('Unsupported or invalid problem configuration');
  }
  const source = Buffer.from(data.source_b64, 'base64');
  if (!source.length || source.length > 102400) throw new Error('Invalid source size');
  await fs.mkdir(WORK, {recursive:true});
  const folder = await fs.mkdtemp(path.join(WORK, 'job-'));
  try {
    const sourceName=data.ext === '.c' ? 'source.c' : 'source.cpp';
    await fs.writeFile(path.join(folder, sourceName), source);
    const subtasks = [];
    for (let i = 0; i < data.cases.length; i++) {
      const c = data.cases[i];
      if (![c.input, c.output].every(n => typeof n === 'string' && path.basename(n) === n && n !== '.' && n !== '..') ||
          !Number.isInteger(c.score) || c.score < 0) throw new Error('Invalid case metadata');
      for (const [name, target] of [[c.input, `${i+1}.in`], [c.output, `${i+1}.out`]]) {
        const original = path.join(ROOT, 'problems', data.pid, 'testdata', name);
        if (!(await fs.lstat(original)).isFile()) throw new Error('Case must be an ordinary file');
        await fs.copyFile(original, path.join(folder, target));
      }
      subtasks.push({id:i+1, type:'min', score:c.score, cases:[{id:i+1, input:`${i+1}.in`, output:`${i+1}.out`}]});
    }
    const cases = [], compilerTexts = [], messages = [];
    let final;
    const session = {
      config: {detail:'full', host:'csp', trusted:false},
      cacheOpen: async () => folder,
      fetchFile: async (namespace) => {
        if (namespace !== null) throw new Error('Unexpected remote file fetch');
        return path.join(folder, sourceName);
      },
      getLang: () => ({key:data.ext === '.c' ? 'c11' : 'cpp14', code_file:sourceName, target:'program',
        compile:data.ext === '.c' ? '/usr/bin/gcc -O2 -std=c11 -static source.c -o program'
          : '/usr/bin/g++ -O2 -std=c++14 -static source.cpp -o program',
        execute:'./program', compile_time_limit:30000, compile_memory_limit:1024,
        time_limit_rate:1, memory_limit_rate:1, process_limit:64}),
      getReporter: () => ({
        next: (event) => {
          if (event.case) cases.push(event.case);
          if (event.compilerText) compilerTexts.push(event.compilerText);
          if (event.message) messages.push(event.message);
        },
        end: (event) => { final = event; },
      }),
    };
    const task = new JudgeTask(session, {
      rid:crypto.randomUUID(), domainId:'csp', uid:1, source:data.pid,
      lang:'cpp14', code:'', files:{code:'local-source'}, data:[], meta:{}, rejudged:true,
      trusted:false, config:{type:'default', checker_type:'default',
        filename:data.io_mode === 'file' ? data.code : undefined,
        time:`${data.time_ms}ms`, memory:`${data.memory_mb}mb`, subtasks, detail:'full'},
    });
    await task.handle();
    if (!final || !Number.isInteger(final.status)) throw new Error('HydroJudge did not finish');
    return {...final, cases:cases.sort((a,b)=>a.id-b.id), compilerTexts, messages,
      engine:{name:'HydroJudge',version, checker:'default', compileMemoryMiB:1024}};
  } finally {
    await fs.rm(folder, {recursive:true, force:true});
  }
}

function reply(res, status, value) {
  const body = JSON.stringify(value); res.writeHead(status, {'Content-Type':'application/json','Content-Length':Buffer.byteLength(body)}); res.end(body);
}
const server = http.createServer(async (req,res) => {
  if (req.method === 'GET' && req.url === '/health') return reply(res,200,{engine:'HydroJudge',version});
  const received = Buffer.from(req.headers.authorization || '');
  const expected = Buffer.from('Bearer ' + TOKEN);
  if (received.length !== expected.length || !crypto.timingSafeEqual(received,expected)) return reply(res,403,{error:'Forbidden'});
  if (req.method !== 'POST' || req.url !== '/judge') return reply(res,404,{error:'Not found'});
  try {
    let size=0; const chunks=[];
    for await (const chunk of req) {
      size+=chunk.length; if (size>1048576) throw new Error('Request too large'); chunks.push(chunk);
    }
    const data=JSON.parse(Buffer.concat(chunks).toString('utf8'));
    const result=pending.then(()=>judge(data)); pending=result.catch(()=>{});
    reply(res,200,await result);
  } catch (error) { reply(res,500,{error:error.message}); }
});
server.requestTimeout=0;
server.listen(Number(process.env.CSP_HYDRO_PORT || 8082),'127.0.0.1',()=>console.log(`HydroJudge ${version} adapter ready`));
