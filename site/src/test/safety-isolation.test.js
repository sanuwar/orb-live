import { fireEvent, render, screen } from '@testing-library/react'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { basename, dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { createElement } from 'react'
import App from '../App'

const sourceRoot = join(dirname(fileURLToPath(import.meta.url)), '..')

function productionFiles(directory) {
  return readdirSync(directory)
    .flatMap((name) => {
      if (name === 'test') return []
      const path = join(directory, name)
      return statSync(path).isDirectory() ? productionFiles(path) : [path]
    })
    .filter((path) => /\.(js|jsx|css)$/.test(path))
}

function productionSource(excluded = []) {
  return productionFiles(sourceRoot)
    .filter((path) => !excluded.includes(basename(path)))
    .map((path) => readFileSync(path, 'utf8'))
    .join('\n')
}

describe('safety isolation', () => {
  it('keeps broker clients and all non-allowlisted network calls unreachable', () => {
    const sourceWithoutAllowlistedClients = productionSource(['localControlClient.js', 'remoteClient.js'])
    const allSource = productionSource()
    const prohibitedEverywhere = [
      /\bXMLHttpRequest\b/,
      /\bWebSocket\b/,
      /\baxios\b/i,
      /\bsession\.call_tool\b/,
      /\bRobinhoodClient\b/,
      /\bMCPClient\b/,
      /\bfrom\s+['"](?:\.\.\/)*src\b/,
      /\bimport\s+src\b/,
      /node:child_process/,
      /\b(?:exec|execFile|spawn|fork)\s*\(/,
    ]

    expect(sourceWithoutAllowlistedClients).not.toMatch(/\bfetch\s*\(/)
    prohibitedEverywhere.forEach((pattern) => expect(allSource).not.toMatch(pattern))
  })

  it('limits the network exception to four fixed loopback operations', () => {
    const source = readFileSync(join(sourceRoot, 'operations', 'localControlClient.js'), 'utf8')
    expect(source).toContain("http://127.0.0.1:8766/api/local-control")
    expect(source).toContain("request('/status')")
    expect(source).toContain("request('/readiness', 'POST')")
    expect(source).toContain("request('/start', 'POST')")
    expect(source).toContain("request('/stop', 'POST')")
    expect(source).not.toMatch(/0\.0\.0\.0|execute|run-command|shell|script|pid/i)
  })

  it('limits the remote client to same-origin auth and read-only monitoring routes', () => {
    const source = readFileSync(join(sourceRoot, 'remote', 'remoteClient.js'), 'utf8')
    expect(source).toContain("'/api/remote/session'")
    expect(source).toContain("'/api/remote/projection'")
    expect(source).toContain("'/api/auth/login'")
    expect(source).toContain("'/api/auth/logout'")
    expect(source).not.toMatch(/127\.0\.0\.1:8766|api\/local-control|readiness|start|stop|broker|commission/i)
  })

  it('contains no credential paths or fingerprint-like values', () => {
    const source = productionSource()
    expect(source).not.toMatch(/\b[a-f0-9]{64}\b/i)
    expect(source).not.toMatch(/\.rh_tokens\.json/i)
    expect(source).not.toMatch(/config[\\/]local\.yaml/i)
    expect(source).not.toMatch(/\bRUN_ID\b/)
    expect(source).not.toMatch(
      /\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b/i,
    )
  })

  it('shows commissioning warnings and does not claim verified protection', () => {
    window.location.hash = '#/safety'
    render(createElement(App))

    expect(screen.getByText(/Checkpoint 7C remains blocked/)).toBeVisible()
    expect(screen.getByText(/active protective-stop verification is unresolved/i)).toBeVisible()
    expect(screen.getByText('No grounded mapping yet')).toBeVisible()
    expect(screen.getByRole('heading', { name: /0 grounded active-state mappings/i })).toBeVisible()
  })

  it('labels the dashboard as documentation rather than a trading console', () => {
    render(createElement(App))

    expect(screen.getByRole('heading', { name: /documentation.not a trading console/i })).toBeVisible()
    expect(screen.getByText(/Not financial advice/)).toBeVisible()
    expect(screen.queryByRole('button', { name: /buy|sell|place|cancel/i })).not.toBeInTheDocument()
  })

  it('filters the glossary entirely in local component state', () => {
    window.location.hash = '#/architecture-glossary'
    render(createElement(App))

    const input = screen.getByRole('searchbox', { name: 'Filter terms' })
    fireEvent.change(input, { target: { value: 'idempotency' } })

    expect(screen.getByText('1 term shown')).toBeVisible()
    expect(screen.getByText('Idempotency')).toBeVisible()
    expect(screen.queryByText('Opening range')).not.toBeInTheDocument()
  })

  it('includes a reduced-motion override', () => {
    const css = readFileSync(join(sourceRoot, 'styles', 'global.css'), 'utf8')
    expect(css).toMatch(/@media\s*\(prefers-reduced-motion:\s*reduce\)/)
  })
})
