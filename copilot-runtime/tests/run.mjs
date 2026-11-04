import {readdirSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
const filter=process.argv[2]??'';
const files=readdirSync(new URL('.',import.meta.url)).filter(name=>name.endsWith('.test.ts')&&name.includes(filter)).map(name=>'tests/'+name);
if(!files.length)throw new Error('no matching runtime tests');
const result=spawnSync(process.execPath,['--import','tsx','--import','./tests/env.mjs','--test',...files],{stdio:'inherit',cwd:new URL('..',import.meta.url)});
process.exit(result.status??1);
