/**
 * codex-lb plugin: desktop half.
 *
 * A "Codex-LB" page (sidebar nav) and a status-bar chip. Data comes from this plugin's own
 * backend (dashboard/plugin_api.py) through ctx.rest, so the codex-lb API key never reaches
 * the renderer. SDK components and theme variables only.
 */

import {
  Badge, Button, ConfirmDialog, EmptyState, host, relativeTime, ROUTES_AREA, SIDEBAR_NAV_AREA,
  STATUSBAR_AREAS, StatusDot, Tip, useMutation, useQuery, useQueryClient
} from '@hermes/plugin-sdk'
import { useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'codex-lb'
const PATH = '/codex-lb'
const POLL_MS = 60000

let rest = null

const pct = value => (typeof value === 'number' ? `${Math.round(value)}%` : '–')
const num = value => (typeof value === 'number' ? value.toLocaleString() : '–')

function toneForRemaining(value) {
  if (typeof value !== 'number') return 'muted'
  if (value < 10) return 'bad'
  if (value < 30) return 'warn'
  return 'good'
}

function useStatus() {
  return useQuery({ queryKey: [ID, 'status'], queryFn: () => rest('/status'), refetchInterval: POLL_MS, retry: 1 })
}

function useUsage(enabled = true) {
  return useQuery({
    queryKey: [ID, 'usage'], queryFn: () => rest('/usage'), enabled,
    refetchInterval: POLL_MS, staleTime: 30000, retry: 1
  })
}

function Section({ title, children }) {
  return jsxs('section', {
    className: 'flex flex-col gap-2 rounded-md border border-(--ui-stroke-secondary) p-3',
    children: [jsx('h3', { className: 'text-xs font-medium text-(--ui-text-secondary)', children: title }), children]
  })
}

function Row({ label, value, tone }) {
  return jsxs('div', {
    className: 'flex items-center justify-between gap-4 text-sm',
    children: [
      jsxs('span', {
        className: 'flex items-center gap-2 text-(--ui-text-tertiary)',
        children: [tone ? jsx(StatusDot, { tone }) : null, label]
      }),
      jsx('span', { className: 'tabular-nums', children: value })
    ]
  })
}

function PoolSection({ usage }) {
  const pool = usage?.pool_remaining_percent || {}
  return jsx(Section, {
    title: 'Pool remaining',
    children: jsxs('div', {
      className: 'flex flex-col gap-1.5',
      children: [
        jsx(Row, { label: '5-hour window', value: pct(pool.primary), tone: toneForRemaining(pool.primary) }),
        jsx(Row, { label: 'Weekly window', value: pct(pool.secondary), tone: toneForRemaining(pool.secondary) })
      ]
    })
  })
}

function TotalsSection({ usage }) {
  const cached = usage?.total_tokens ? (usage.cached_input_tokens / usage.total_tokens) * 100 : null
  return jsx(Section, {
    title: 'Totals (this API key)',
    children: jsxs('div', {
      className: 'flex flex-col gap-1.5',
      children: [
        jsx(Row, { label: 'Requests', value: num(usage?.requests) }),
        jsx(Row, { label: 'Tokens', value: num(usage?.total_tokens) }),
        jsx(Row, { label: 'Cached share of tokens', value: pct(cached) }),
        jsx(Row, {
          label: 'Estimated API-price cost',
          value: typeof usage?.cost_usd === 'number' ? `$${usage.cost_usd.toFixed(2)}` : '–'
        })
      ]
    })
  })
}

function TierSection({ tiers }) {
  if (!tiers) return null
  const last = tiers.last
  return jsx(Section, {
    title: 'Fast tier (requested vs served)',
    children: jsxs('div', {
      className: 'flex flex-col gap-1.5',
      children: [
        jsx(Row, { label: 'Priority requested', value: num(tiers.priority_requested) }),
        jsx(Row, {
          label: 'Priority served',
          value: num(tiers.priority_served),
          tone: tiers.priority_requested ? (tiers.downgraded ? 'warn' : 'good') : 'muted'
        }),
        tiers.downgraded
          ? jsx('p', {
              className: 'text-xs text-(--ui-text-tertiary)',
              children: `${tiers.downgraded} priority request(s) came back at another tier. Turn fast off with /lbfast off if it isn't being honoured.`
            })
          : null,
        last
          ? jsx(Row, { label: 'Last request', value: `asked ${last.requested}, served ${last.served}` })
          : null
      ]
    })
  })
}

function CreditsSection({ canRedeem }) {
  const client = useQueryClient()
  const credits = useQuery({ queryKey: [ID, 'credits'], queryFn: () => rest('/reset-credits'), refetchInterval: POLL_MS, retry: 1 })
  const [pending, setPending] = useState(null)
  const redeem = useMutation({
    mutationFn: credit => rest('/reset-credits/redeem', {
      method: 'POST', body: { account_id: credit.account_id, redeem_id: credit.redeem_id }
    }),
    onSuccess: result => {
      host.notify({ kind: 'success', message: `Reset credit redeemed for ${result.account}` })
      client.invalidateQueries({ queryKey: [ID] })
    }
  })

  const list = credits.data?.credits || []
  return jsxs(Section, {
    title: 'Banked reset credits',
    children: [
      credits.isError
        ? jsx('p', { className: 'text-xs text-(--ui-text-tertiary)', children: String(credits.error?.message || credits.error) })
        : null,
      !credits.isError && list.length === 0
        ? jsx('p', { className: 'text-xs text-(--ui-text-tertiary)', children: credits.isLoading ? 'Loading…' : 'None available.' })
        : null,
      ...list.map(credit => jsxs('div', {
        className: 'flex items-center justify-between gap-3 text-sm',
        children: [
          jsxs('span', {
            className: 'flex flex-col',
            children: [
              jsx('span', { children: credit.account }),
              credit.expires_at
                ? jsx('span', { className: 'text-xs text-(--ui-text-tertiary)', children: `expires ${relativeTime(credit.expires_at)}` })
                : null
            ]
          }),
          canRedeem
            ? jsx(Button, { size: 'sm', variant: 'outline', onClick: () => setPending(credit), children: 'Redeem' })
            : jsx(Tip, { label: 'Set Access mode to read_write in Settings → Plugins → codex-lb', children: jsx(Badge, { variant: 'muted', children: 'read-only' }) })
        ]
      }, credit.redeem_id)),
      jsx(ConfirmDialog, {
        open: Boolean(pending),
        onClose: () => setPending(null),
        destructive: true,
        title: 'Redeem reset credit?',
        description: pending
          ? `This spends the banked reset for ${pending.account} now and resets its usage window. It can't be undone.`
          : '',
        confirmLabel: 'Redeem',
        busyLabel: 'Redeeming…',
        onConfirm: async () => {
          await redeem.mutateAsync(pending)
        }
      })
    ]
  })
}

function CodexLbPage() {
  const status = useStatus()
  const usage = useUsage(Boolean(status.data?.configured))

  if (status.isLoading) return jsx('div', { className: 'p-6 text-sm text-(--ui-text-tertiary)', children: 'Loading…' })
  if (!status.data?.configured) {
    return jsx('div', {
      className: 'p-6',
      children: jsx(EmptyState, {
        title: 'codex-lb is not set up',
        description: 'Add your codex-lb base URL and an API key in Settings → Plugins → codex-lb.'
      })
    })
  }

  return jsxs('div', {
    className: 'flex h-full flex-col gap-3 overflow-auto p-4',
    children: [
      jsxs('header', {
        className: 'flex items-center justify-between',
        children: [
          jsx('h2', { className: 'text-base font-semibold', children: 'Codex-LB' }),
          jsxs('div', {
            className: 'flex items-center gap-2',
            children: [
              jsx(Badge, { variant: 'outline', children: status.data.mode === 'read_write' ? 'read/write' : 'read-only' }),
              jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => { status.refetch(); usage.refetch() }, children: 'Refresh' })
            ]
          })
        ]
      }),
      usage.isError
        ? jsx('p', { className: 'text-sm text-(--ui-text-tertiary)', children: String(usage.error?.message || usage.error) })
        : null,
      jsxs('div', {
        className: 'grid gap-3 md:grid-cols-2',
        children: [
          jsx(PoolSection, { usage: usage.data }),
          jsx(TierSection, { tiers: status.data.tiers }),
          jsx(TotalsSection, { usage: usage.data }),
          jsx(CreditsSection, { canRedeem: status.data.mode === 'read_write' })
        ]
      }),
      jsx('p', {
        className: 'text-xs text-(--ui-text-quaternary)',
        children: 'Use /lbfast on|off|auto in a chat to pick the fast tier for that session.'
      })
    ]
  })
}

function CodexLbChip() {
  const status = useStatus()
  const usage = useUsage(Boolean(status.data?.configured))
  if (!status.data?.configured) return null
  const primary = usage.data?.pool_remaining_percent?.primary
  const tiers = status.data.tiers || {}
  const label = `5h ${pct(primary)} · wk ${pct(usage.data?.pool_remaining_percent?.secondary)}`
  return jsx(Tip, {
    label: tiers.priority_requested
      ? `codex-lb pool remaining · priority served ${tiers.priority_served}/${tiers.priority_requested}`
      : 'codex-lb pool remaining',
    children: jsxs('button', {
      type: 'button',
      className: 'inline-flex h-full items-center gap-1.5 px-1.5 text-[0.6875rem] text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground',
      onClick: () => host.navigate(PATH),
      children: [jsx(StatusDot, { tone: toneForRemaining(primary) }), label]
    })
  })
}

export default {
  id: ID,
  name: 'Codex-LB',
  register(ctx) {
    rest = ctx.rest
    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: PATH }, render: () => jsx(CodexLbPage, {}) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: PATH, label: 'Codex-LB', codicon: 'pulse' } },
      { id: 'chip', area: STATUSBAR_AREAS.right, order: 150, render: () => jsx(CodexLbChip, {}) }
    ])
  }
}
