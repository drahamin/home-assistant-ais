const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const tvScript = fs.readFileSync(new URL('../web/tv.js', `file://${__dirname}/`), 'utf8');
const match = tvScript.match(/function parseAisTime\(value\)\{[^\n]+\}/);
assert.ok(match, 'TV map must define parseAisTime');

const context = {};
vm.runInNewContext(`${match[0]}; this.parseAisTime = parseAisTime;`, context);

assert.equal(context.parseAisTime('2026-08-15T11:43:32'), Date.parse('2026-08-15T11:43:32Z'));
assert.equal(context.parseAisTime('2026-08-15T11:43:32+02:00'), Date.parse('2026-08-15T11:43:32+02:00'));
assert.equal(context.parseAisTime('1786794212'), 1786794212000);
assert.ok(Number.isNaN(context.parseAisTime('')));

console.log('AIS timestamp normalization tests passed');
