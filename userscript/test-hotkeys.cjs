// No packages required: node userscript/test-hotkeys.cjs
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(__dirname + '/audiochan-save-list.user.js', 'utf8');
const normalize = source.slice(source.indexOf('    const normalize ='), source.indexOf('    const kind ='));
const handlers = source.slice(source.indexOf('    function setCurrent('), source.indexOf("    listButton.addEventListener"));
const saved = new Map();
let keydown, writes = 0;
class Element {
    constructor(editable = false, input = false) { this.isContentEditable = editable; this.input = input; }
    matches() { return this.input; }
}
const context = {
    URL, HTMLElement: Element,
    location: { href: '' }, document: { querySelector: () => null, title: 'Example - audiochan' },
    selected: url => saved.get(url) || false,
    write: (url, value) => { saved.set(url, value); writes++; },
    refresh() {}, notify() {}, kind: () => 'audio',
    toggle: { addEventListener() {} },
    window: { addEventListener: (name, fn) => { if (name === 'keydown') keydown = fn; } }
};
vm.runInNewContext(normalize + handlers, context);
function press(code, extra = {}) {
    let prevented = false;
    keydown({ code, composedPath: () => [], preventDefault: () => { prevented = true; }, stopImmediatePropagation() {}, ...extra });
    return prevented;
}
for (const path of ['a/TestAudio', 's/TestScript', 'u/Seven_Veils_']) {
    const url = 'https://audiochan.com/' + path;
    context.location.href = url + '?c=comment';
    assert.equal(press('NumpadAdd'), true);
    assert.equal(saved.get(url), true);
    const before = writes;
    press('NumpadAdd');
    assert.equal(writes, before);
    assert.equal(saved.get(url), true);
    press('NumpadSubtract');
    assert.equal(saved.get(url), false);
    press('NumpadSubtract');
    assert.equal(saved.get(url), false);
}
context.location.href = 'https://audiochan.com/a/TestAudio';
for (const options of [{ ctrlKey: true }, { altKey: true }, { shiftKey: true }, { metaKey: true }, { isComposing: true }, { composedPath: () => [new Element(true)] }, { composedPath: () => [new Element(false, true)] }]) {
    assert.equal(press('NumpadAdd', options), false);
}
const before = writes;
press('NumpadAdd', { repeat: true });
press('Equal');
press('Minus');
assert.equal(writes, before);
for (const url of ['https://audiochan.com/', 'https://audiochan.com/settings', 'https://other.example/a/TestAudio']) {
    context.location.href = url;
    assert.equal(press('NumpadAdd'), false);
}
console.log('OK: NumPad add/remove, all three page types, repeated keys, typing and modifiers.');
