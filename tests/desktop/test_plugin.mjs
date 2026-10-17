/** Synthetic component tests for #10 / PR15. Executes the complete disk module with
 * SDK/React hook doubles. This does not qualify real React, CSS or Desktop rendering.
 * Run: node --experimental-vm-modules --test tests/desktop/test_plugin.mjs
 */
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import { SourceTextModule, SyntheticModule, createContext } from 'node:vm'

const source = new URL('../../desktop/plugin.js', import.meta.url)

async function harness(overrides = {}) {
  const queries = {
    status: { data: { configured: true, mode: 'read_only', tiers: {} } },
    usage: { data: { pool_remaining_percent: { primary: null, secondary: 83 } } },
    credits: { data: { credits: [], can_redeem: false } },
    ...overrides
  }
  const queried = []
  const navigations = []
  const contributions = []
  const jsx = (type, props) => ({ type, props })
  const sdk = {
    ...Object.fromEntries(['Badge', 'Button', 'ConfirmDialog', 'EmptyState', 'StatusDot', 'Tip'].map(n => [n, n])),
    ROUTES_AREA: 'routes', SIDEBAR_NAV_AREA: 'sidebar.nav', STATUSBAR_AREAS: { right: 'statusBar.right' },
    host: { navigate: path => navigations.push(path), notify() {} },
    relativeTime: value => value,
    useQuery(options) {
      queried.push(options)
      const name = options.queryKey[1]
      assert.ok(name in queries, `unexpected query ${name}`)
      return { isLoading: false, isError: false, refetch() {}, ...queries[name] }
    },
    useQueryClient: () => ({ invalidateQueries() {} }),
    useMutation: () => ({ mutateAsync: () => { throw new Error('unexpected mutation') } })
  }
  const context = createContext({})
  const modules = new Map()
  for (const [name, exports] of Object.entries({
    '@hermes/plugin-sdk': sdk,
    react: { useState: initial => [initial, () => {}] },
    'react/jsx-runtime': { jsx, jsxs: jsx }
  })) {
    modules.set(name, new SyntheticModule(Object.keys(exports), function () {
      for (const [key, value] of Object.entries(exports)) this.setExport(key, value)
    }, { context }))
  }
  const module = new SourceTextModule(await readFile(source, 'utf8'), { context, identifier: source.href })
  await module.link(name => {
    assert.ok(modules.has(name), `unsupported disk-plugin import ${name}`)
    return modules.get(name)
  })
  await module.evaluate()
  module.namespace.default.register({
    rest() { throw new Error('unexpected backend request in synthetic test') },
    registerMany: entries => contributions.push(...entries)
  })
  function expand(node) {
    if (node == null || typeof node !== 'object') return node
    if (Array.isArray(node)) return node.map(expand)
    if (typeof node.type === 'function') return expand(node.type(node.props))
    return { ...node, props: { ...node.props, children: expand(node.props.children) } }
  }
  return {
    contributions, queried, navigations,
    render: id => expand(contributions.find(c => c.id === id).render())
  }
}

function nodes(tree, predicate) {
  if (tree == null || typeof tree !== 'object') return []
  if (Array.isArray(tree)) return tree.flatMap(n => nodes(n, predicate))
  return [...(predicate(tree) ? [tree] : []), ...nodes(tree.props.children, predicate)]
}

function text(tree) {
  if (tree == null) return ''
  if (Array.isArray(tree)) return tree.map(text).join(' ')
  if (typeof tree !== 'object') return String(tree)
  return text(tree.props.children)
}

for (const [remaining, tone] of [[83, 'good'], [20, 'warn'], [5, 'bad']]) {
  test(`weekly-only chip shows weekly ${remaining}% with ${tone} tone`, async () => {
    const h = await harness({ usage: { data: { pool_remaining_percent: { primary: null, secondary: remaining } } } })
    const chip = h.render('chip')
    assert.equal(text(chip).trim(), `wk ${remaining}%`)
    assert.equal(nodes(chip, n => n.type === 'StatusDot')[0].props.tone, tone)
    nodes(chip, n => n.type === 'button')[0].props.onClick()
    assert.deepEqual(h.navigations, ['/codex-lb'])
  })
}

test('a real zero primary window stays visible and determines chip tone', async () => {
  const h = await harness({ usage: { data: { pool_remaining_percent: { primary: 0, secondary: 83 } } } })
  const chip = h.render('chip')
  assert.match(text(chip), /5h 0% · wk 83%/)
  assert.equal(nodes(chip, n => n.type === 'StatusDot')[0].props.tone, 'bad')
  const page = text(h.render('page'))
  assert.match(page, /5-hour window 0%/)
  assert.doesNotMatch(page, /No 5-hour/)
})

test('weekly-only page explains absent window and labels the cached denominator', async () => {
  const h = await harness({ usage: { data: {
    pool_remaining_percent: { primary: null, secondary: 83 }, total_tokens: 200, cached_input_tokens: 50
  } } })
  const page = text(h.render('page'))
  assert.match(page, /No 5-hour window in this pool\./)
  assert.match(page, /Weekly window 83%/)
  assert.match(page, /Cached input \/ all tokens 25%/)
  assert.doesNotMatch(page, /Cached share of tokens/)
})

test('backend failure is not described as missing configuration', async () => {
  const h = await harness({ status: { isError: true, error: new Error('synthetic 502') } })
  const page = h.render('page')
  const empty = nodes(page, n => n.type === 'EmptyState')[0].props
  assert.equal(empty.title, 'codex-lb plugin backend unavailable')
  assert.equal(empty.description, 'synthetic 502')
  assert.equal(h.queried.find(q => q.queryKey[1] === 'usage').enabled, false)
})

test('unconfigured state stays separate and the chip stays hidden', async () => {
  const h = await harness({ status: { data: { configured: false } } })
  assert.equal(nodes(h.render('page'), n => n.type === 'EmptyState')[0].props.title, 'codex-lb is not set up')
  assert.equal(h.render('chip'), null)
})

test('joined page keeps current-main reported-tier semantics', async () => {
  const h = await harness({ status: { data: { configured: true, mode: 'read_only', tiers: {
    priority_requested: 2, priority_reported: 0, priority_reported_other: 1, priority_unreported: 1,
    last: { requested: 'priority', served: 'unknown' }
  } } } })
  const page = text(h.render('page'))
  assert.match(page, /1 reported another tier, 1 reported none/)
  assert.match(page, /asked priority, reported none/)
  assert.doesNotMatch(page, /downgraded|served every priority request/)
})
