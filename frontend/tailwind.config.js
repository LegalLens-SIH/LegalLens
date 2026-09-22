/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./*.html', './assets/**/*.js'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        primary: '#00274a',
        'primary-container': '#0b3d6b',
        secondary: '#006a6a',
        'secondary-container': '#90efef',
        'secondary-fixed': '#93f2f2',
        'secondary-fixed-dim': '#76d6d5',
        background: '#f8f9fa',
        surface: '#f8f9fa',
        'surface-container-lowest': '#ffffff',
        'surface-container-low': '#f3f4f5',
        'surface-container': '#edeeef',
        'surface-container-high': '#e7e8e9',
        'surface-container-highest': '#e1e3e4',
        'surface-variant': '#e1e3e4',
        'surface-dim': '#d9dadb',
        'surface-bright': '#f8f9fa',
        'on-surface': '#191c1d',
        'on-surface-variant': '#42474f',
        'on-background': '#191c1d',
        'on-primary': '#ffffff',
        'on-primary-container': '#82a9dd',
        'on-primary-fixed': '#001c38',
        'on-primary-fixed-variant': '#1c4877',
        'on-secondary': '#ffffff',
        'on-secondary-container': '#006e6e',
        'on-secondary-fixed': '#002020',
        'on-secondary-fixed-variant': '#004f4f',
        outline: '#737780',
        'outline-variant': '#c3c6d0',
        error: '#ba1a1a',
        'error-container': '#ffdad6',
        'on-error': '#ffffff',
        'on-error-container': '#93000a',
        tertiary: '#332300',
        'tertiary-container': '#4e3800',
        'tertiary-fixed': '#ffdfa0',
        'tertiary-fixed-dim': '#fbbc00',
        'on-tertiary': '#ffffff',
        'on-tertiary-container': '#d39d00',
        'on-tertiary-fixed': '#261a00',
        'on-tertiary-fixed-variant': '#5c4300',
        'inverse-surface': '#2e3132',
        'inverse-primary': '#a2c9ff'
      },
      borderRadius: {
        DEFAULT: '0.125rem',
        lg: '0.25rem',
        xl: '0.5rem',
        full: '0.75rem'
      },
      spacing: {
        'sidebar-width': '260px',
        'container-max': '1440px',
        unit: '4px',
        'margin-page': '32px',
        gutter: '24px'
      },
      fontFamily: {
        sans: ['Inter', 'sans-serif'],
        'headline-lg': ['Inter', 'sans-serif'],
        'headline-md': ['Inter', 'sans-serif'],
        'headline-sm': ['Inter', 'sans-serif']
      }
    }
  },
  plugins: [
    require('@tailwindcss/forms'),
    require('@tailwindcss/container-queries')
  ]
};
