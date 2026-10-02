'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const ts=require('../frontend/node_modules/typescript');
const source=path.resolve(__dirname,'../frontend/src');
const english=JSON.parse(fs.readFileSync(path.join(source,'lib/translations/en.ts'),'utf8').split('= ').slice(1).join('= '));
const fields=text=>[...new Set(text.match(/\{[a-zA-Z0-9_]+\}/g)||[])].sort();
for(const [key,value] of Object.entries(english)) {
 assert(value.trim()&&!/[가-힣]/.test(value),'Missing English translation: '+key);
 assert.deepEqual(fields(value),fields(key),'Interpolation mismatch: '+key);
}
function files(dir){return fs.readdirSync(dir,{withFileTypes:true}).flatMap(f=>f.isDirectory()?files(path.join(dir,f.name)):[path.join(dir,f.name)]);}
for(const file of files(source).filter(f=>/\.tsx?$/.test(f)&&!f.includes('/translations/'))) {
 const ast=ts.createSourceFile(file,fs.readFileSync(file,'utf8'),ts.ScriptTarget.Latest,true);
 function visit(n){
  if(ts.isStringLiteral(n)&&/[가-힣]/.test(n.text))assert(Object.hasOwn(english,n.text),'Untranslated message in '+file+': '+n.text);
  if(ts.isJsxText(n)&&/[가-힣]/.test(n.text))assert.equal(n.text.trim(),'한국어','Untranslated JSX in '+file);
  ts.forEachChild(n,visit);
 }
 visit(ast);
}
console.log('Translation catalog checked:',Object.keys(english).length,'messages');
