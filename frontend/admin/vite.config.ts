import { defineConfig } from 'vitest/config';
import tailwindcss from '@tailwindcss/vite';
import { sveltekit } from '@sveltejs/kit/vite';
import config from './kit.config.js';

export default defineConfig({
	plugins: [tailwindcss(), sveltekit(config)],
	test: {
		environment: 'node',
		include: ['tests/**/*.test.ts'],
		expect: { requireAssertions: true }
	}
});
