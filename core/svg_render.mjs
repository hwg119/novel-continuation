// Static SVG only; stdin/stdout isolate rendering from project paths and URLs.
import { createRequire } from 'node:module'
const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { Resvg } = require('@resvg/resvg-js')
if (!process.argv.includes('--check')) {
  let input = ''
  for await (const chunk of process.stdin) {
    input += chunk
    if (input.length > 600000) throw new Error('SVG input exceeds limit')
  }
  const { svg, scale } = JSON.parse(input)
  if (![1, 2, 3, 4].includes(scale)) throw new Error('Invalid render scale')
  const renderer = new Resvg(svg, {
    background: '#ffffff',
    fitTo: { mode: 'width', value: 1200 * scale },
    font: { loadSystemFonts: false },
  })
  process.stdout.write(renderer.render().asPng())
}
