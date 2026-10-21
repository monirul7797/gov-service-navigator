import { describe, expect, test } from 'vitest';
import { compile } from 'svelte/compiler';
import config from '../kit.config.js';

describe('svelte and sveltekit configuration', () => {
	test('runes mode is enforced', () => {
		expect(config.compilerOptions?.runes).toBe(true);
	});

	test('legacy props syntax fails to compile in runes mode', () => {
		expect(() => compile('<script>export let x;</script>{x}', { runes: true })).toThrow();
	});

	test('csp uses hashes, never unsafe-inline scripts', () => {
		expect(config.csp?.mode).toBe('hash');
		expect(config.csp?.directives?.['script-src']).toEqual(['self']);
	});

	test('no csp directive allows unsafe-inline or unsafe-eval', () => {
		const sources = Object.values(config.csp?.directives ?? {}).flat();
		expect(sources.length).toBeGreaterThan(0);
		expect(sources).not.toContain('unsafe-inline');
		expect(sources).not.toContain('unsafe-eval');
	});
});
