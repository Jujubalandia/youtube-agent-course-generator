#!/usr/bin/env node
'use strict';

function fail(msg) {
  console.error('ERROR: ' + msg);
  process.exit(1);
}

const inputPath = process.argv[2];
const outputPath = process.argv[3];
if (!inputPath || !outputPath) fail('usage: ua-tour-analyze.js <input.json> <output.json>');

let raw;
try {
  raw = require('fs').readFileSync(inputPath, 'utf8');
} catch (e) {
  fail('cannot read input file: ' + e.message);
}

let data;
try {
  data = JSON.parse(raw);
} catch (e) {
  fail('invalid JSON in input file: ' + e.message);
}

const nodes = Array.isArray(data.nodes) ? data.nodes : fail('input missing nodes array');
const edges = Array.isArray(data.edges) ? data.edges : fail('input missing edges array');
const layers = Array.isArray(data.layers) ? data.layers : [];

const nodeById = new Map();
for (const n of nodes) nodeById.set(n.id, n);

// A. Fan-in / B. Fan-out
const fanIn = new Map();
const fanOut = new Map();
for (const n of nodes) { fanIn.set(n.id, 0); fanOut.set(n.id, 0); }
for (const e of edges) {
  if (nodeById.has(e.source)) fanOut.set(e.source, (fanOut.get(e.source) || 0) + 1);
  if (nodeById.has(e.target)) fanIn.set(e.target, (fanIn.get(e.target) || 0) + 1);
}

function topN(map, n, key) {
  return [...map.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, n)
    .map(([id, val]) => ({ id, [key]: val, name: (nodeById.get(id) || {}).name || id }));
}

const fanInRanking = topN(fanIn, 20, 'fanIn');
const fanOutRanking = topN(fanOut, 20, 'fanOut');

// C. Entry point candidates
const ENTRY_FILENAMES = new Set([
  'index.ts', 'index.js', 'main.ts', 'main.js', 'app.ts', 'app.js', 'server.ts', 'server.js',
  'mod.rs', 'main.go', 'main.py', 'main.rs', 'manage.py', 'app.py', 'wsgi.py', 'asgi.py',
  'run.py', '__main__.py', 'Application.java', 'Main.java', 'Program.cs', 'config.ru',
  'index.php', 'App.swift', 'Application.kt', 'main.cpp', 'main.c'
]);

const fanOutVals = [...fanOut.values()].sort((a, b) => a - b);
const fanInVals = [...fanIn.values()].sort((a, b) => a - b);
function percentileThreshold(sortedVals, pct) {
  if (sortedVals.length === 0) return 0;
  const idx = Math.min(sortedVals.length - 1, Math.floor(sortedVals.length * pct));
  return sortedVals[idx];
}
const fanOutTop10Threshold = percentileThreshold(fanOutVals, 0.9);
const fanInBottom25Threshold = percentileThreshold(fanInVals, 0.25);

function pathDepth(filePath) {
  return filePath.split('/').filter(Boolean).length;
}

const entryScores = [];
for (const n of nodes) {
  let score = 0;
  const fp = n.filePath || '';
  const base = fp.split('/').pop();
  if (n.type === 'document') {
    if (base === 'README.md' && pathDepth(fp) === 1) score += 5;
    else if (base && base.endsWith('.md') && pathDepth(fp) === 1) score += 2;
  } else {
    if (ENTRY_FILENAMES.has(base)) score += 3;
    if (pathDepth(fp) <= 2) score += 1;
    if ((fanOut.get(n.id) || 0) >= fanOutTop10Threshold && (fanOut.get(n.id) || 0) > 0) score += 1;
    if ((fanIn.get(n.id) || 0) <= fanInBottom25Threshold) score += 1;
  }
  if (score > 0) entryScores.push({ id: n.id, score, name: n.name, summary: n.summary });
}
entryScores.sort((a, b) => b.score - a.score);
const entryPointCandidates = entryScores.slice(0, 5);

// D. BFS from top code entry point (skip document nodes)
const topCodeEntry = entryScores.find(e => {
  const n = nodeById.get(e.id);
  return n && n.type !== 'document';
});

const adjacency = new Map();
for (const n of nodes) adjacency.set(n.id, []);
for (const e of edges) {
  if ((e.type === 'imports' || e.type === 'calls') && adjacency.has(e.source) && nodeById.has(e.target)) {
    adjacency.get(e.source).push(e.target);
  }
}

let bfsTraversal = { startNode: null, order: [], depthMap: {}, byDepth: {} };
if (topCodeEntry) {
  const start = topCodeEntry.id;
  const visited = new Set([start]);
  const order = [start];
  const depthMap = { [start]: 0 };
  const queue = [start];
  while (queue.length) {
    const cur = queue.shift();
    const d = depthMap[cur];
    for (const next of (adjacency.get(cur) || [])) {
      if (!visited.has(next)) {
        visited.add(next);
        depthMap[next] = d + 1;
        order.push(next);
        queue.push(next);
      }
    }
  }
  const byDepth = {};
  for (const [id, d] of Object.entries(depthMap)) {
    byDepth[d] = byDepth[d] || [];
    byDepth[d].push(id);
  }
  bfsTraversal = { startNode: start, order, depthMap, byDepth };
}

// E. Non-code file inventory
const nonCodeFiles = { documentation: [], infrastructure: [], data: [], config: [] };
for (const n of nodes) {
  const entry = { id: n.id, name: n.name, type: n.type, summary: n.summary };
  if (n.type === 'document') nonCodeFiles.documentation.push(entry);
  else if (n.type === 'service' || n.type === 'pipeline' || n.type === 'resource') nonCodeFiles.infrastructure.push(entry);
  else if (n.type === 'table' || n.type === 'schema' || n.type === 'endpoint') nonCodeFiles.data.push(entry);
  else if (n.type === 'config') nonCodeFiles.config.push(entry);
}

// F. Tightly coupled clusters
const bidirPairs = new Map(); // key "a|b" (sorted) -> true
const directedEdgeSet = new Set();
for (const e of edges) {
  if (e.type === 'imports' || e.type === 'calls') {
    directedEdgeSet.add(e.source + '->' + e.target);
  }
}
const pairEdgeCount = new Map();
for (const e of edges) {
  if (e.type !== 'imports' && e.type !== 'calls') continue;
  const rev = e.target + '->' + e.source;
  if (directedEdgeSet.has(rev)) {
    const key = [e.source, e.target].sort().join('|');
    pairEdgeCount.set(key, (pairEdgeCount.get(key) || 0) + 1);
  }
}

// build initial clusters from bidirectional pairs
const clusterMap = new Map(); // node -> cluster set id
let clusters = [];
for (const key of pairEdgeCount.keys()) {
  const [a, b] = key.split('|');
  // find existing cluster containing a or b
  let target = clusters.find(c => c.has(a) || c.has(b));
  if (!target) {
    target = new Set();
    clusters.push(target);
  }
  target.add(a);
  target.add(b);
}
// merge overlapping clusters
let merged = true;
while (merged) {
  merged = false;
  outer:
  for (let i = 0; i < clusters.length; i++) {
    for (let j = i + 1; j < clusters.length; j++) {
      const a = clusters[i], b = clusters[j];
      if ([...a].some(x => b.has(x))) {
        for (const x of b) a.add(x);
        clusters.splice(j, 1);
        merged = true;
        break outer;
      }
    }
  }
}
// expand: add nodes connecting to 2+ existing cluster members (forward or reverse imports/calls)
const allDirectedEdges = edges.filter(e => e.type === 'imports' || e.type === 'calls');
for (const cluster of clusters) {
  let expanded = true;
  while (expanded && cluster.size < 5) {
    expanded = false;
    const counts = new Map();
    for (const e of allDirectedEdges) {
      if (cluster.has(e.source) && !cluster.has(e.target)) {
        counts.set(e.target, (counts.get(e.target) || 0) + 1);
      } else if (cluster.has(e.target) && !cluster.has(e.source)) {
        counts.set(e.source, (counts.get(e.source) || 0) + 1);
      }
    }
    for (const [id, cnt] of counts.entries()) {
      if (cnt >= 2 && cluster.size < 5) {
        cluster.add(id);
        expanded = true;
      }
    }
  }
}
// compute edge count within each cluster and format
function edgeCountWithin(clusterSet) {
  let count = 0;
  for (const e of edges) {
    if (clusterSet.has(e.source) && clusterSet.has(e.target)) count++;
  }
  return count;
}
const clusterOutputs = clusters
  .filter(c => c.size >= 2 && c.size <= 5)
  .map(c => ({ nodes: [...c], edgeCount: edgeCountWithin(c) }))
  .sort((a, b) => b.edgeCount - a.edgeCount)
  .slice(0, 10);

// G. Layer list
const layerList = { count: layers.length, list: layers.map(l => ({ id: l.id, name: l.name, description: l.description })) };

// H. Node summary index
const nodeSummaryIndex = {};
for (const n of nodes) {
  nodeSummaryIndex[n.id] = { name: n.name, type: n.type, summary: n.summary };
}

const result = {
  scriptCompleted: true,
  entryPointCandidates,
  fanInRanking,
  fanOutRanking,
  bfsTraversal,
  nonCodeFiles,
  clusters: clusterOutputs,
  layers: layerList,
  nodeSummaryIndex,
  totalNodes: nodes.length,
  totalEdges: edges.length
};

try {
  require('fs').writeFileSync(outputPath, JSON.stringify(result, null, 2));
} catch (e) {
  fail('cannot write output file: ' + e.message);
}

process.exit(0);
