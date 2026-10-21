import adapter from '@sveltejs/adapter-static';

/**
 * Svelte + SvelteKit options, passed to the `sveltekit()` Vite plugin in `vite.config.ts` and
 * imported by `tests/config.test.ts`.
 *
 * SvelteKit 3 no longer reads `svelte.config.js` (it refuses to start if one exists), so the
 * options live in this module instead of the file earlier SvelteKit versions used.
 *
 * @type {import('@sveltejs/kit/vite').Config}
 */
const config = {
	// Svelte 5 runes mode for every component. Legacy Svelte 4 syntax (`export let`, `$:`, `on:`)
	// then fails the build instead of being silently accepted.
	compilerOptions: { runes: true },

	// Static output: `npm run build` writes `build/index.html` plus assets, served by the Rust `web`
	// service. Every route must be prerendered (`prerender = true` in `src/routes/+layout.ts`).
	adapter: adapter(),

	// Hash-based Content-Security-Policy, written into a <meta http-equiv> tag of each prerendered
	// page. SvelteKit adds a sha256 hash for each inline script it emits, so `script-src` never needs
	// 'unsafe-inline'. `style-src 'self'` allows stylesheet files only: no inline <style> blocks and
	// no `style=` attributes.
	csp: {
		mode: 'hash',
		directives: {
			'default-src': ['self'],
			'script-src': ['self'],
			'style-src': ['self'],
			'img-src': ['self', 'data:'],
			'font-src': ['self'],
			'connect-src': ['self'],
			'object-src': ['none'],
			'base-uri': ['self'],
			'form-action': ['self']
		}
	}
};

export default config;
