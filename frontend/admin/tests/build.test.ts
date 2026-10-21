import { createHash } from 'node:crypto';
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, test } from 'vitest';

// Runs after `npm run build` (see the `test:build` script).
const LANG = 'en';
const html = readFileSync(path.resolve(import.meta.dirname, '../build/index.html'), 'utf8');
const csp = html.match(/http-equiv="content-security-policy" content="([^"]+)"/i)?.[1] ?? '';

describe('built index.html', () => {
	test('index.html has language and a hash-based CSP meta', () => {
		expect(html).toContain(`<html lang="${LANG}"`);
		expect(csp).toMatch(/script-src 'self'( 'sha256-[^']+')+/);
		expect(csp).not.toContain('unsafe-inline');
	});

	test('every inline script is covered by a sha256 hash in the CSP', () => {
		const inline = [...html.matchAll(/<script(?![^>]*\ssrc=)[^>]*>([\s\S]*?)<\/script>/gi)].map(
			(m) => m[1]
		);
		expect(inline.length).toBeGreaterThan(0);
		for (const body of inline) {
			const hash = createHash('sha256').update(body).digest('base64');
			expect(csp).toContain(`'sha256-${hash}'`);
		}
	});

	test('no inline styles that style-src self would block', () => {
		expect(html).not.toMatch(/<style[\s>]/i);
		expect(html).not.toMatch(/\sstyle=/i);
	});

	test("the route announcer's hiding rules are in the stylesheet, not only inline", () => {
		// SvelteKit adds #svelte-announcer with an inline style, which style-src 'self' blocks.
		const dir = path.resolve(import.meta.dirname, '../build/_app/immutable/assets');
		const css = readdirSync(dir)
			.filter((name) => name.endsWith('.css'))
			.map((name) => readFileSync(path.join(dir, name), 'utf8'))
			.join('\n');
		expect(css).toMatch(/#svelte-announcer\{[^}]*position:absolute/);
		expect(css).toMatch(/#svelte-announcer\{[^}]*clip-path:inset\(50%\)/);
	});
});
