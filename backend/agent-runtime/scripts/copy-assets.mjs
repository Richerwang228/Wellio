import {cpSync,mkdirSync} from 'node:fs'; mkdirSync('dist/prompts',{recursive:true}); cpSync('src/prompts/wellio.md','dist/prompts/wellio.md');
