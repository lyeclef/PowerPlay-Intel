const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');
const Module = require('module');
const babel = require('@babel/core');
const appRoot = path.resolve(__dirname, '..');
const originalResolve = Module._resolveFilename;
Module._resolveFilename = function(request, parent, ...args) {
  return originalResolve.call(this, request.startsWith('@/') ? path.join(appRoot, 'src', request.slice(2)) : request, parent, ...args);
};
for (const extension of ['.js', '.jsx']) {
  const original = Module._extensions[extension] || Module._extensions['.js'];
  Module._extensions[extension] = (module, filename) => {
    if (!filename.startsWith(path.join(appRoot, 'src'))) return original(module, filename);
    const {code} = babel.transformSync(fs.readFileSync(filename, 'utf8'), {
      filename, babelrc:false, configFile:false,
      presets:[[require.resolve('@babel/preset-react'), {runtime:'automatic'}]],
      plugins:[require.resolve('@babel/plugin-transform-modules-commonjs')],
    });
    module._compile(code, filename);
  };
}
const React = require('react');
const {renderToStaticMarkup} = require('react-dom/server');
const {SmartPickBanner} = require('../src/components/terminal/SmartPickBanner.jsx');
const {EventCard} = require('../src/components/terminal/EventCard.jsx');
const pick = {side:'YES', outcome:'Team A', conviction:'MODERATE', sharpCount:0,
  candidateCount:0, underdogCount:2, smartCapital:1000, avgEntry:null, entryStatus:'unknown',
  entryCoverage:0, slippageCents:null};
const analysis = {sharpPick:null, pick, leanSide:'UNAVAILABLE', combined:{pick, leanSide:'YES'},
  sharpCount:0, candidateCount:0, underdogCount:2, participantCount:2};
function banner(mode, change={}) {
  const value = {...analysis, combined:{...analysis.combined, pick:{...pick,...change}}};
  return renderToStaticMarkup(React.createElement(SmartPickBanner, {analysis:value, measureMode:mode}));
}
assert(!banner('sharp').includes('data-testid="bet-on-polymarket-btn"'), 'Sharp-only mode must not borrow a combined pick');
assert(banner('combined').includes('Historical entry unknown'));
assert(!banner('combined').includes('Better than smart entry'));
assert(banner('combined').includes('2 Underdogs'));
assert(banner('combined', {avgEntry:.4, entryStatus:'partial', entryCoverage:.5}).includes('Entry measured for 50%'));
const event = {title:'Team A vs Team B', markets:[{id:'m', outcomes:['A','B'], prices:[.6,.4],
  marketType:'moneyline', analysis:{...analysis, combined:{...analysis.combined, strengthYes:100, strengthNo:0}}}]};
const card = renderToStaticMarkup(React.createElement(EventCard, {event, marketType:'moneyline', measureMode:'combined', onSelect:()=>{}}));
assert(card.includes('2 UNDERDOG'), 'Underdogs must display even when candidate count is zero');
console.log('6 frontend render checks passed');
