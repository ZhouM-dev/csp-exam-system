const assert = require('node:assert/strict');
const {validate, walk, writeFormData} = require('../web/static/submission.js');
const file = (name, text='code') => new File([text], name);
const entries = [
  {file:file('number.cpp'), path:'GD-J12345/number/number.cpp'},
  {file:file('学生甲.TXT', '姓名：学生甲'), path:'GD-J12345/学生甲.TXT'}
];
async function main() {
  validate(entries, 1000);
  const data = new FormData(); data.append('files', file('stale.cpp')); data.append('zipfile', file('stale.zip'));
  writeFormData(data, entries);
  assert.deepEqual(data.getAll('files').map(f=>f.name), entries.map(e=>e.path));
  assert.equal(data.has('zipfile'), false);
  writeFormData(data, [{file:file('答案.ZIP'), path:'答案.ZIP'}]);
  assert.deepEqual(data.getAll('files').map(f=>f.name), ['答案.ZIP']);
  assert.throws(()=>validate(entries.concat(entries[0]),1000), /重复/);
  assert.throws(()=>validate([{file:file('x'),path:'../x'}],1000), /路径/);
  assert.throws(()=>validate(entries,1), /大小/);
  assert.throws(()=>validate([],1000), /为空/);
  assert.throws(()=>validate(Array.from({length:1025},(_,i)=>({file:file('x'),path:'root/'+i})),100000), /数量/);
  function leaf(name) {return {name,isFile:true,file:resolve=>resolve(file(name))};}
  let reads = 0;
  const root = {name:'GD-J12345',isDirectory:true,createReader:()=>({readEntries(resolve){
    reads++;
    resolve(reads===1 ? Array.from({length:100},(_,i)=>leaf('file'+i+'.TXT')) : reads===2 ? [leaf('学生甲.TXT')] : []);
  }})};
  const result = await walk(root,10000);
  assert.equal(reads,3); assert.equal(result.length,101);
  assert.equal(result.at(-1).path,'GD-J12345/学生甲.TXT');
  console.log('PASS: folder paths/TXT, ZIP replacement, multipart filenames, duplicate/path/size/count guards, multi-batch directory reads.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
