/** @type {import('tailwindcss').Config} */
module.exports = {
    content: [
        "./index.html",
        "./src/**/*.{js,ts,jsx,tsx}",
    ],
    darkMode: 'class',
    theme: {
        extend: {
            colors: {
                slate: {
                    850: '#1e293b'
                }
            },
            keyframes: {
                'retail-pulse': {
                    '0%, 100%': { transform: 'scale(1)', opacity: '1' },
                    '50%': { transform: 'scale(1.06)', opacity: '0.82' },
                },
                'retail-shelf': {
                    '0%, 100%': { opacity: '0.35', transform: 'scaleY(0.92)' },
                    '50%': { opacity: '1', transform: 'scaleY(1)' },
                },
                'retail-shimmer': {
                    '0%': { transform: 'translateX(-120%)' },
                    '100%': { transform: 'translateX(320%)' },
                },
            },
            animation: {
                'retail-pulse': 'retail-pulse 0.75s ease-in-out infinite',
                'retail-shelf': 'retail-shelf 0.65s ease-in-out infinite',
                'retail-shimmer': 'retail-shimmer 0.85s ease-in-out infinite',
            },
        },
    },
    plugins: [],
}
