#!/usr/bin/env node
/**
 * 双端能力基线的零依赖静态检查。
 * 只证明登记、文件、路由、API 字符串和权限点未发生明显漂移；不代替 UI/E2E。
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const matrixPath = path.join(root, 'scripts', 'capability-matrix.json');
const matrix = JSON.parse(fs.readFileSync(matrixPath, 'utf8'));
const errors = [];
const statuses = new Set(['supported', 'partial', 'planned', 'native-only', 'not-applicable']);
const requireComplete = process.argv.includes('--require-complete');
const fail = (id, message) => errors.push(`${id}: ${message}`);
const absolute = (relative) => path.join(root, ...relative.split('/'));

function sourceFiles(directory, extensions) {
  if (!fs.existsSync(directory)) return [];
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(target, extensions);
    return extensions.has(path.extname(entry.name)) ? [target] : [];
  });
}

function readSources(files) {
  return files.map((file) => fs.readFileSync(file, 'utf8')).join('\n');
}

const app = JSON.parse(fs.readFileSync(absolute('frontend/app.json'), 'utf8'));
const miniRoutes = new Set(app.pages || []);
for (const subpackage of app.subPackages || app.subpackages || []) {
  const prefix = subpackage.root.replace(/\/$/, '');
  for (const page of subpackage.pages || []) miniRoutes.add(`${prefix}/${page}`);
}

const miniSources = readSources(sourceFiles(absolute('frontend/services'), new Set(['.ts'])));
const webFiles = sourceFiles(absolute('frontend-web/src'), new Set(['.vue', '.js', '.ts']));
const webSources = readSources(webFiles);
const permissionSource = fs.readFileSync(absolute('backend/apps/core/permissions.py'), 'utf8');
const registeredPermissions = new Set(
  [...permissionSource.matchAll(/['"]key['"]\s*:\s*['"]([^'"]+)['"]/g)].map((match) => match[1])
);

// 从 App.vue 沿 Vue import 图检查组件可达，避免留下未被任何页面使用的孤儿文件。
const webEntry = absolute('frontend-web/src/App.vue');
const reachableWeb = new Set();
function visitWeb(file) {
  const normalized = path.normalize(file);
  if (reachableWeb.has(normalized) || !fs.existsSync(normalized)) return;
  reachableWeb.add(normalized);
  const source = fs.readFileSync(normalized, 'utf8');
  const imports = source.matchAll(/(?:import\s+[^;]*?\s+from\s*|import\s*\()\s*['"]([^'"]+\.vue)['"]/g);
  for (const match of imports) {
    if (match[1].startsWith('.')) visitWeb(path.resolve(path.dirname(normalized), match[1]));
  }
}
visitWeb(webEntry);

const seen = new Set();
const coveredMiniRoutes = new Set();
const coveredWebEntries = new Set();
for (const capability of matrix.capabilities || []) {
  const id = capability.id || '<missing-id>';
  if (!capability.id || !capability.name) fail(id, '缺少 id 或 name');
  if (seen.has(id)) fail(id, '能力 id 重复');
  seen.add(id);
  if (requireComplete && (capability.web?.status !== 'supported' || capability.miniprogram?.status !== 'supported')) {
    fail(id, `完整对齐门禁：Web=${capability.web?.status || 'missing'}，小程序=${capability.miniprogram?.status || 'missing'}`);
  }

  for (const permission of capability.permissions || []) {
    if (!registeredPermissions.has(permission)) fail(id, `权限点未在后端注册: ${permission}`);
  }

  for (const platformName of ['web', 'miniprogram']) {
    const platform = capability[platformName];
    if (!platform || !statuses.has(platform.status)) {
      fail(id, `${platformName} 状态缺失或不合法`);
      continue;
    }
    if (['not-applicable', 'planned', 'partial'].includes(platform.status) && !platform.reason) {
      fail(id, `${platformName}=${platform.status} 必须说明 reason`);
    }
    if (!['supported', 'partial', 'native-only'].includes(platform.status)) continue;

    if (platformName === 'miniprogram') {
      const routes = [...new Set([platform.route, ...(platform.routes || [])].filter(Boolean))];
      if (!routes.length) {
        fail(id, '小程序实现缺少 route/routes');
      }
      for (const route of routes) {
        coveredMiniRoutes.add(route);
        if (!miniRoutes.has(route)) fail(id, `小程序路由未注册: ${route}`);
        for (const extension of ['ts', 'wxml', 'wxss', 'json']) {
          const pageFile = `frontend/${route}.${extension}`;
          if (!fs.existsSync(absolute(pageFile))) fail(id, `小程序页面文件缺失: ${pageFile}`);
        }
      }
    } else {
      const entries = [...new Set([platform.entry, ...(platform.entries || [])].filter(Boolean))];
      if (!entries.length) fail(id, 'Web 实现缺少 entry/entries');
      for (const entry of entries) {
        coveredWebEntries.add(path.normalize(absolute(entry)));
        if (!fs.existsSync(absolute(entry))) fail(id, `Web 入口文件缺失: ${entry}`);
        else if (!reachableWeb.has(path.normalize(absolute(entry)))) {
          fail(id, `Web 入口未从 App.vue 可达: ${entry}`);
        }
      }
    }

    if (platform.status === 'supported') {
      const sources = platformName === 'web' ? webSources : miniSources;
      for (const api of capability.apis || []) {
        const clientPath = api.replace(/^\/api/, '');
        if (!sources.includes(`'${clientPath}`) && !sources.includes(`\"${clientPath}`) && !sources.includes(`\`${clientPath}`)) {
          fail(id, `${platformName} 未找到已登记 API: ${api}`);
        }
      }
    }
  }
}

const excludedMiniRoutes = new Set();
for (const item of matrix.route_exclusions || []) {
  if (!item.route || !item.reason) fail('miniprogram.coverage', '路由排除项必须包含 route 和 reason');
  if (!miniRoutes.has(item.route)) fail('miniprogram.coverage', `排除的路由不存在: ${item.route}`);
  excludedMiniRoutes.add(item.route);
}
const uncoveredMiniRoutes = [...miniRoutes].filter((route) => (
  !coveredMiniRoutes.has(route) && !excludedMiniRoutes.has(route)
)).sort();
const uncoveredWebViews = [...reachableWeb].filter((file) => (
  file.includes(`${path.sep}src${path.sep}views${path.sep}`) && !coveredWebEntries.has(file)
)).map((file) => path.relative(root, file).replaceAll(path.sep, '/')).sort();
if (requireComplete) {
  for (const route of uncoveredMiniRoutes) fail('miniprogram.coverage', `页面未登记双端能力: ${route}`);
  for (const entry of uncoveredWebViews) fail('web.coverage', `页面未登记双端能力: ${entry}`);
}

const summary = {
  capabilities: (matrix.capabilities || []).length,
  supportedPairs: (matrix.capabilities || []).filter((item) => (
    item.web?.status === 'supported' && item.miniprogram?.status === 'supported'
  )).length,
  nativeOnly: (matrix.capabilities || []).filter((item) => (
    item.web?.status === 'native-only' || item.miniprogram?.status === 'native-only'
  )).length,
  incompletePairs: (matrix.capabilities || []).filter((item) => (
    item.web?.status !== 'supported' || item.miniprogram?.status !== 'supported'
  )).map((item) => item.id),
  requireComplete,
  uncoveredMiniRoutes,
  uncoveredWebViews,
  errors
};

if (process.argv.includes('--json')) {
  console.log(JSON.stringify(summary, null, 2));
} else {
  console.log(`能力矩阵: ${summary.capabilities} 项；双端支持 ${summary.supportedPairs} 项；原生例外 ${summary.nativeOnly} 项${requireComplete ? '；完整对齐门禁已启用' : ''}。`);
  if (errors.length) console.error(errors.map((error) => `SYNC ${error}`).join('\n'));
  else console.log('双端能力静态同步检查通过（不代替 UI、真机或端到端验收）。');
}
process.exitCode = errors.length ? 1 : 0;
