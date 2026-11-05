import './env.mjs';
import { readFileSync } from 'node:fs';
import { createRequire, registerHooks } from 'node:module';

const versions = new Set();
registerHooks({
  load(url, context, nextLoad) {
    if (/\/zod\/v4\/index\.(?:js|cjs)$/.test(url)) {
      versions.add(JSON.parse(readFileSync(new URL('../package.json', url), 'utf8')).version);
    }
    return nextLoad(url, context);
  },
});

process.on('message', () => {
  const require = createRequire(import.meta.url);
  const client = createRequire(require.resolve('@ag-ui/client'));
  const { z } = client('zod/v4');
  let visits = 0;
  const result = z.array(z.string().refine(() => { visits++; return true; })).max(2).safeParse(['a', 'b', 'c']);
  process.send({ versions: [...versions], cjsVersion: z.core.version, visits,
    issues: result.error?.issues.length, legacyVersion: client('zod/package.json').version });
});
