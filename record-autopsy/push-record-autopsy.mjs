// push-record-autopsy.mjs — 把 record-autopsy 推到 xun-li99/record-autopsy（Contents API，幂等）
// 需要环境变量 GH_TOKEN（classic PAT，scope=repo；或 fine-grained: Contents RW + Administration RW）。
// 不打印 token。已存在的文件走 update（先 GET 取 sha），不存在则 create。
import { readFileSync, existsSync } from 'node:fs';
import { resolve, basename } from 'node:path';

const TOKEN = process.env.GH_TOKEN || (() => {
  // 会话内更可靠的通路：新进程继承的是父进程环境，改用户级环境变量未必立刻生效。
  // 所以允许从文件读（该文件不进仓库、不进网络、不打印）。
  try {
    const p = resolve(process.env.USERPROFILE || '', '.dsh', 'gh-token.txt');
    return existsSync(p) ? readFileSync(p, 'utf8').trim() : '';
  } catch { return ''; }
})();
const API = 'https://api.github.com';
const OWNER = 'xun-li99';
const REPO = `${OWNER}/record-autopsy`;
const DIR = resolve('C:\\Users\\Mechrevo\\Desktop\\女娲系统\\源-传承\\03-实践\\record-autopsy');
const FILES = ['README.md', 'FIELD-NOTE.md', 'autopsy.py', 'example-signal.txt',
               'example-session.txt', 'example-signal.json'];
const H = {
  Authorization: `Bearer ${TOKEN}`,
  Accept: 'application/vnd.github+json',
  'User-Agent': 'yuan-nuwa',
  'X-GitHub-Api-Version': '2022-11-28',
};

if (!TOKEN) {
  console.error('NO_TOKEN: set GH_TOKEN, or write the token to %USERPROFILE%\\.dsh\\gh-token.txt');
  console.error('(this script never prints the token itself)');
  process.exit(2);
}

async function api(path, opts = {}) {
  const res = await fetch(API + path, { ...opts, headers: { ...H, ...(opts.headers || {}) } });
  const body = res.status === 204 ? null : await res.json().catch(() => null);
  return { status: res.status, body };
}

// 0. 验身份（只打 login，不打 token）
const me = await api('/user');
if (me.status !== 200) { console.error('AUTH_FAILED', me.status); process.exit(1); }
console.log('auth ok as', me.body?.login);

// 1. 仓库不存在则建
let repo = await api(`/repos/${REPO}`);
if (repo.status === 404) {
  const created = await api('/user/repos', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: 'record-autopsy',
      description: 'Measure what a long-running agent record actually contains — instead of reading it and believing it.',
      homepage: '', private: false, has_issues: true, has_wiki: false,
    }),
  });
  if (created.status !== 201) { console.error('CREATE_FAILED', created.status, JSON.stringify(created.body).slice(0, 300)); process.exit(1); }
  console.log('repo created');
} else {
  console.log('repo exists');
}

// 2. 逐个文件 create/update
for (const name of FILES) {
  const full = resolve(DIR, name);
  if (!existsSync(full)) { console.log('SKIP (missing locally):', name); continue; }
  const content = readFileSync(full);
  const existing = await api(`/repos/${REPO}/contents/${name}`);
  const sha = existing.status === 200 ? existing.body.sha : undefined;
  const put = await api(`/repos/${REPO}/contents/${name}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message: (sha ? 'Update ' : 'Add ') + name + ' (源, 2026-09-11)',
      content: content.toString('base64'),
      ...(sha ? { sha } : {}),
    }),
  });
  console.log(put.status, name, put.body?.commit?.sha?.slice(0, 8) || JSON.stringify(put.body).slice(0, 120));
}

const head = await api(`/repos/${REPO}/commits?per_page=1`);
console.log('head commit:', head.body?.[0]?.sha?.slice(0, 8), '|', head.body?.[0]?.commit?.message);
console.log('url: https://github.com/' + REPO);
