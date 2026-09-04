#!/usr/bin/env node
// Structural analysis script for architecture-analyzer (Phase 1)
const fs = require('fs');
const path = require('path');

function fail(msg) {
  console.error('ERROR: ' + msg);
  process.exit(1);
}

const inPath = process.argv[2];
const outPath = process.argv[3];
if (!inPath || !outPath) fail('usage: node ua-arch-analyze.js <input.json> <output.json>');

let data;
try {
  data = JSON.parse(fs.readFileSync(inPath, 'utf8'));
} catch (e) {
  fail('failed to read/parse input: ' + e.message);
}

const fileNodes = data.fileNodes || [];
const importEdgesRaw = data.importEdges || [];
const allEdgesRaw = data.allEdges || [];

const nodeById = new Map(fileNodes.map(n => [n.id, n]));
const nodeIds = new Set(fileNodes.map(n => n.id));

// Only keep edges where both endpoints are file-level nodes (present in fileNodes)
const importEdges = importEdgesRaw.filter(e => nodeIds.has(e.source) && nodeIds.has(e.target));
const allEdges = allEdgesRaw.filter(e => nodeIds.has(e.source) && nodeIds.has(e.target));

// ---------- A. Directory Grouping ----------
function dirOf(fp) {
  const idx = fp.lastIndexOf('/');
  return idx === -1 ? '' : fp.substring(0, idx);
}

const filePaths = fileNodes.map(n => n.filePath || n.name || '');

function commonPrefix(paths) {
  if (paths.length === 0) return '';
  const split = paths.map(p => p.split('/'));
  let prefix = split[0];
  for (let i = 1; i < split.length; i++) {
    const cur = split[i];
    let j = 0;
    while (j < prefix.length && j < cur.length && prefix[j] === cur[j]) j++;
    prefix = prefix.slice(0, j);
    if (prefix.length === 0) break;
  }
  // don't include the last segment if it's a filename (no trailing dir separator concept needed since we compare segments)
  return prefix;
}

// Compute common prefix considering only directory segments (exclude filename)
const dirSegmentsList = fileNodes.map(n => {
  const fp = n.filePath || n.name || '';
  const segs = fp.split('/');
  return segs.slice(0, -1); // directory segments only
});
let commonDirPrefix = [];
if (dirSegmentsList.length > 0) {
  commonDirPrefix = dirSegmentsList[0].slice();
  for (let i = 1; i < dirSegmentsList.length; i++) {
    const cur = dirSegmentsList[i];
    let j = 0;
    while (j < commonDirPrefix.length && j < cur.length && commonDirPrefix[j] === cur[j]) j++;
    commonDirPrefix = commonDirPrefix.slice(0, j);
    if (commonDirPrefix.length === 0) break;
  }
}

function groupKeyFor(node) {
  const fp = node.filePath || node.name || '';
  const segs = fp.split('/');
  const dirSegs = segs.slice(0, -1);
  const rest = dirSegs.slice(commonDirPrefix.length);
  if (rest.length > 0) return rest[0];
  // No subdirectory beyond common prefix -> group by root dir segment if any, else flat/extension
  if (dirSegs.length > 0) return dirSegs[0];
  // flat file at root with no directory: group by extension pattern
  const name = segs[segs.length - 1];
  if (/\.(test|spec)\.[a-z]+$/.test(name) || /^test_/.test(name) || /_test\.[a-z]+$/.test(name)) return 'test';
  if (/\.config\./.test(name) || /^\.env/.test(name)) return 'config';
  const extMatch = name.match(/\.([a-zA-Z0-9]+)$/);
  return extMatch ? '_root_' + extMatch[1] : '_root_other';
}

const directoryGroups = {};
for (const node of fileNodes) {
  const key = groupKeyFor(node);
  if (!directoryGroups[key]) directoryGroups[key] = [];
  directoryGroups[key].push(node.id);
}

// ---------- B. Node Type Grouping ----------
const nodeTypeGroups = {};
for (const node of fileNodes) {
  const t = node.type || 'file';
  if (!nodeTypeGroups[t]) nodeTypeGroups[t] = [];
  nodeTypeGroups[t].push(node.id);
}

// ---------- C. Import Adjacency Matrix ----------
const fileFanOut = {};
const fileFanIn = {};
const importAdj = new Map(); // id -> Set(targets)
for (const node of fileNodes) {
  fileFanOut[node.id] = 0;
  fileFanIn[node.id] = 0;
}
for (const e of importEdges) {
  fileFanOut[e.source] = (fileFanOut[e.source] || 0) + 1;
  fileFanIn[e.target] = (fileFanIn[e.target] || 0) + 1;
  if (!importAdj.has(e.source)) importAdj.set(e.source, new Set());
  importAdj.get(e.source).add(e.target);
}

const idToGroup = {};
for (const [g, ids] of Object.entries(directoryGroups)) {
  for (const id of ids) idToGroup[id] = g;
}

const groupImportsFrom = {}; // group -> Set(groups it imports from)
const groupImportedBy = {}; // group -> Set(groups that import it)
for (const g of Object.keys(directoryGroups)) {
  groupImportsFrom[g] = new Set();
  groupImportedBy[g] = new Set();
}
for (const e of importEdges) {
  const sg = idToGroup[e.source];
  const tg = idToGroup[e.target];
  if (sg && tg && sg !== tg) {
    groupImportsFrom[sg].add(tg);
    groupImportedBy[tg].add(sg);
  }
}

// ---------- D. Cross-Category Dependency Analysis ----------
const crossCategoryMap = new Map(); // key "fromType|toType|edgeType" -> count
for (const e of allEdges) {
  const sType = nodeById.get(e.source) ? nodeById.get(e.source).type : 'unknown';
  const tType = nodeById.get(e.target) ? nodeById.get(e.target).type : 'unknown';
  if (sType === tType) continue; // only cross-category
  const key = sType + '|' + tType + '|' + e.type;
  crossCategoryMap.set(key, (crossCategoryMap.get(key) || 0) + 1);
}
const crossCategoryEdges = [...crossCategoryMap.entries()].map(([k, count]) => {
  const [fromType, toType, edgeType] = k.split('|');
  return { fromType, toType, edgeType, count };
}).sort((a, b) => b.count - a.count);

// ---------- E. Inter-Group Import Frequency ----------
const interGroupMap = new Map(); // "from|to" -> count
for (const e of importEdges) {
  const sg = idToGroup[e.source];
  const tg = idToGroup[e.target];
  if (sg && tg && sg !== tg) {
    const key = sg + '|' + tg;
    interGroupMap.set(key, (interGroupMap.get(key) || 0) + 1);
  }
}
const interGroupImports = [...interGroupMap.entries()].map(([k, count]) => {
  const [from, to] = k.split('|');
  return { from, to, count };
}).sort((a, b) => b.count - a.count);

// ---------- F. Intra-Group Import Density ----------
const intraGroupDensity = {};
for (const g of Object.keys(directoryGroups)) {
  let internalEdges = 0;
  let totalEdges = 0;
  for (const e of importEdges) {
    const sg = idToGroup[e.source];
    const tg = idToGroup[e.target];
    if (sg === g || tg === g) {
      totalEdges++;
      if (sg === g && tg === g) internalEdges++;
    }
  }
  intraGroupDensity[g] = {
    internalEdges,
    totalEdges,
    density: totalEdges > 0 ? +(internalEdges / totalEdges).toFixed(3) : 0
  };
}

// ---------- G. Directory Pattern Matching ----------
const dirPatternTable = {
  routes: 'api', api: 'api', controllers: 'api', endpoints: 'api', handlers: 'api',
  services: 'service', core: 'service', lib: 'service', domain: 'service', logic: 'service',
  models: 'data', db: 'data', data: 'data', persistence: 'data', repository: 'data', entities: 'data',
  components: 'ui', views: 'ui', pages: 'ui', ui: 'ui', layouts: 'ui', screens: 'ui',
  middleware: 'middleware', plugins: 'middleware', interceptors: 'middleware', guards: 'middleware',
  utils: 'utility', helpers: 'utility', common: 'utility', shared: 'utility', tools: 'utility',
  config: 'config', constants: 'config', env: 'config', settings: 'config',
  __tests__: 'test', test: 'test', tests: 'test', spec: 'test', specs: 'test',
  types: 'types', interfaces: 'types', schemas: 'types', contracts: 'types', dtos: 'types',
  hooks: 'hooks',
  store: 'state', state: 'state', reducers: 'state', actions: 'state', slices: 'state',
  assets: 'assets', static: 'assets', public: 'assets',
  migrations: 'data',
  management: 'config', commands: 'config',
  templatetags: 'utility',
  signals: 'service',
  serializers: 'api',
  cmd: 'entry',
  internal: 'service',
  pkg: 'utility',
  dto: 'types', request: 'types', response: 'types',
  entity: 'data',
  controller: 'api',
  routers: 'api',
  composables: 'service',
  blueprints: 'api',
  mailers: 'service', jobs: 'service', channels: 'service',
  bin: 'entry',
  docs: 'documentation', documentation: 'documentation', wiki: 'documentation',
  deploy: 'infrastructure', deployment: 'infrastructure', infra: 'infrastructure', infrastructure: 'infrastructure',
  '.github': 'ci-cd', '.gitlab': 'ci-cd', '.circleci': 'ci-cd',
  k8s: 'infrastructure', kubernetes: 'infrastructure', helm: 'infrastructure', charts: 'infrastructure',
  terraform: 'infrastructure', tf: 'infrastructure',
  docker: 'infrastructure',
  sql: 'data', database: 'data', schema: 'data',
  scripts: 'infrastructure',
  app: 'service',
};

const patternMatches = {};
for (const g of Object.keys(directoryGroups)) {
  const lower = g.toLowerCase();
  if (dirPatternTable[lower]) {
    patternMatches[g] = dirPatternTable[lower];
  }
}

// ---------- H. Deployment Topology Detection ----------
const infraFiles = [];
let hasDockerfile = false, hasCompose = false, hasK8s = false, hasTerraform = false, hasCI = false;
for (const node of fileNodes) {
  const fp = node.filePath || '';
  const base = path.basename(fp);
  if (/^Dockerfile/.test(base)) { hasDockerfile = true; infraFiles.push(fp); }
  else if (/^docker-compose/.test(base)) { hasCompose = true; infraFiles.push(fp); }
  else if (/\.ya?ml$/.test(base) && /(k8s|kubernetes)/i.test(fp)) { hasK8s = true; infraFiles.push(fp); }
  else if (/\.tf$|\.tfvars$/.test(base)) { hasTerraform = true; infraFiles.push(fp); }
  else if (/^\.github\/workflows\//.test(fp) || base === '.gitlab-ci.yml' || base === 'Jenkinsfile') { hasCI = true; infraFiles.push(fp); }
  else if (base === 'Makefile') { infraFiles.push(fp); }
}

const deploymentTopology = {
  hasDockerfile, hasCompose, hasK8s, hasTerraform, hasCI,
  infraFiles: [...new Set(infraFiles)]
};

// ---------- I. Data Pipeline Detection ----------
const schemaFiles = [];
const migrationFiles = [];
const dataModelFiles = [];
const apiHandlerFiles = [];
for (const node of fileNodes) {
  const fp = node.filePath || '';
  const base = path.basename(fp);
  if (/\.sql$/.test(base) || /\.graphql$/.test(base) || /\.gql$/.test(base) || /\.proto$/.test(base) || /\.prisma$/.test(base)) schemaFiles.push(fp);
  if (/migrations\//.test(fp)) migrationFiles.push(fp);
  const g = idToGroup[node.id];
  const tags = node.tags || [];
  if (patternMatches[g] === 'data' || tags.includes('data-model') || tags.includes('schema-definition')) dataModelFiles.push(fp);
  if (patternMatches[g] === 'api' || tags.includes('api-handler')) apiHandlerFiles.push(fp);
}

const dataPipeline = {
  schemaFiles: [...new Set(schemaFiles)],
  migrationFiles: [...new Set(migrationFiles)],
  dataModelFiles: [...new Set(dataModelFiles)],
  apiHandlerFiles: [...new Set(apiHandlerFiles)]
};

// ---------- J. Documentation Coverage ----------
const docNodes = fileNodes.filter(n => n.type === 'document');
const groupsWithDocsSet = new Set();
for (const doc of docNodes) {
  const g = idToGroup[doc.id];
  if (g) groupsWithDocsSet.add(g);
}
const totalGroupsCount = Object.keys(directoryGroups).length;
const groupsWithDocsCount = groupsWithDocsSet.size;
const undocumentedGroups = Object.keys(directoryGroups).filter(g => !groupsWithDocsSet.has(g));
const docCoverage = {
  groupsWithDocs: groupsWithDocsCount,
  totalGroups: totalGroupsCount,
  coverageRatio: totalGroupsCount > 0 ? +(groupsWithDocsCount / totalGroupsCount).toFixed(3) : 0,
  undocumentedGroups
};

// ---------- K. Dependency Direction ----------
const dependencyDirection = [];
const seenPairs = new Set();
for (const { from, to, count } of interGroupImports) {
  const pairKey = [from, to].sort().join('|');
  if (seenPairs.has(pairKey)) continue;
  seenPairs.add(pairKey);
  const reverseCount = interGroupMap.get(to + '|' + from) || 0;
  if (count > reverseCount) {
    dependencyDirection.push({ dependent: from, dependsOn: to });
  } else if (reverseCount > count) {
    dependencyDirection.push({ dependent: to, dependsOn: from });
  }
}

// ---------- fileStats ----------
const filesPerGroup = {};
for (const [g, ids] of Object.entries(directoryGroups)) filesPerGroup[g] = ids.length;
const nodeTypeCounts = {};
for (const [t, ids] of Object.entries(nodeTypeGroups)) nodeTypeCounts[t] = ids.length;

const fileStats = {
  totalFileNodes: fileNodes.length,
  filesPerGroup,
  nodeTypeCounts
};

const result = {
  scriptCompleted: true,
  directoryGroups,
  nodeTypeGroups,
  crossCategoryEdges,
  interGroupImports,
  intraGroupDensity,
  patternMatches,
  deploymentTopology,
  dataPipeline,
  docCoverage,
  dependencyDirection,
  fileStats,
  fileFanIn,
  fileFanOut
};

try {
  fs.writeFileSync(outPath, JSON.stringify(result, null, 2));
} catch (e) {
  fail('failed to write output: ' + e.message);
}

console.log('OK: wrote ' + outPath);
process.exit(0);
