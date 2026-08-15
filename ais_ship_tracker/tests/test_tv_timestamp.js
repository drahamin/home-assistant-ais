const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const tvScript = fs.readFileSync(new URL('../web/tv.js', `file://${__dirname}/`), 'utf8');
const match = tvScript.match(/function parseAisTime\(value\)\{[^\n]+\}/);
const seenMatch = tvScript.match(/function vesselSeenAt\(vessel\)\{[^\n]+\}/);
assert.ok(match, 'TV map must define parseAisTime');
assert.ok(seenMatch, 'TV map must define vesselSeenAt');

const context = {};
vm.runInNewContext(`${match[0]}; ${seenMatch[0]}; this.parseAisTime = parseAisTime; this.vesselSeenAt = vesselSeenAt;`, context);

assert.equal(context.parseAisTime('2026-08-15T11:43:32'), Date.parse('2026-08-15T11:43:32Z'));
assert.equal(context.parseAisTime('2026-08-15T11:43:32+02:00'), Date.parse('2026-08-15T11:43:32+02:00'));
assert.equal(context.parseAisTime('1786794212'), 1786794212000);
assert.ok(Number.isNaN(context.parseAisTime('')));
assert.equal(
  context.vesselSeenAt({last_seen: '2026-08-15T13:20:03Z', source_last_seen: '2026-08-15T09:18:25'}),
  Date.parse('2026-08-15T13:20:03Z'),
);
assert.equal(context.vesselSeenAt({source_last_seen: '2026-08-15T09:18:25'}), Date.parse('2026-08-15T09:18:25Z'));

console.log('AIS timestamp normalization and freshness-priority tests passed');
