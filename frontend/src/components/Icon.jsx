/**
 * A small set of line icons, drawn here so the app needs no icon font or
 * extra server (the content policy allows images from this origin only).
 */
const PATHS = {
  radar: 'M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0 -18 0M12 12m-5 0a5 5 0 1 0 10 0a5 5 0 1 0 -10 0M12 12l6.5 -6.5M12 12m-1 0a1 1 0 1 0 2 0a1 1 0 1 0 -2 0',
  people: 'M9 7m-3 0a3 3 0 1 0 6 0a3 3 0 1 0 -6 0M3 20v-1a5 5 0 0 1 5 -5h2a5 5 0 0 1 5 5v1M16 4a3 3 0 0 1 0 6M21 20v-1a5 5 0 0 0 -3.5 -4.8',
  shield: 'M12 3l8 3v6c0 5 -3.5 8 -8 9c-4.5 -1 -8 -4 -8 -9v-6zM9 12l2 2l4 -4',
  download: 'M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2 -2v-2M7 11l5 5l5 -5M12 4v12',
  language: 'M4 5h7M9 3v2c0 4.4 -2.2 8 -5 9M5 9c0 2.1 2.9 3.9 6.4 4M12 20l4 -9l4 9M19.1 18h-6.2',
  chat: 'M4 20l1.3 -3.9a8 8 0 1 1 3 2.9zM8 10h8M8 14h5',
  layers: 'M12 4l8 4l-8 4l-8 -4zM4 12l8 4l8 -4M4 16l8 4l8 -4',
  leaf: 'M5 21c.5 -4.5 2.5 -8 7 -10M9 18c6.2 0 10.5 -3.3 11 -12v-2h-4c-7.2 0 -9.2 3.4 -9 9c0 1 0 3 2 5h0z',
  arrow: 'M5 12h14M13 6l6 6l-6 6',
  map: 'M3 7l6 -3l6 3l6 -3v13l-6 3l-6 -3l-6 3zM9 4v13M15 7v13',
  clock: 'M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0 -18 0M12 7v5l3 3',
  check: 'M5 12l5 5l10 -10',
  water: 'M12 3c3.5 4.5 6 8 6 11a6 6 0 0 1 -12 0c0 -3 2.5 -6.5 6 -11z',
  history: 'M12 8v4l2 2M3.05 11a9 9 0 1 1 .5 4m-.5 5v-5h5',
  help: 'M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0 -18 0M12 17v.01M12 13.5a1.5 1.5 0 0 1 1 -1.5a2.6 2.6 0 1 0 -3 -4',
  home: 'M5 12l-2 0l9 -9l9 9l-2 0M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2 -2v-7M9 21v-6a2 2 0 0 1 2 -2h2a2 2 0 0 1 2 2v6',
  plus: 'M12 5v14M5 12h14',
  user: 'M12 7m-4 0a4 4 0 1 0 8 0a4 4 0 1 0 -8 0M6 21v-2a4 4 0 0 1 4 -4h4a4 4 0 0 1 4 4v2',
  logout: 'M14 8v-2a2 2 0 0 0 -2 -2h-7a2 2 0 0 0 -2 2v12a2 2 0 0 0 2 2h7a2 2 0 0 0 2 -2v-2M9 12h12l-3 -3M18 15l3 -3',
  satellite: 'M3.7 10.7l3 -3a1 1 0 0 1 1.4 0l2.6 2.6a1 1 0 0 1 0 1.4l-3 3a1 1 0 0 1 -1.4 0l-2.6 -2.6a1 1 0 0 1 0 -1.4zM10 10l3.5 3.5M12.7 17.3l3 -3a1 1 0 0 1 1.4 0l2.6 2.6a1 1 0 0 1 0 1.4l-3 3a1 1 0 0 1 -1.4 0l-2.6 -2.6a1 1 0 0 1 0 -1.4zM6 15a3 3 0 0 0 3 3M6 19a7 7 0 0 0 -1 -2',
  menu: 'M4 6h16M4 12h16M4 18h16',
  close: 'M18 6l-12 12M6 6l12 12',
  sparkle: 'M12 3l1.9 5.1l5.1 1.9l-5.1 1.9l-1.9 5.1l-1.9 -5.1l-5.1 -1.9l5.1 -1.9z',
  building: 'M3 21h18M5 21v-14l8 -4v18M19 21v-10l-6 -4M9 9v.01M9 12v.01M9 15v.01M9 18v.01',
  search: 'M10 10m-7 0a7 7 0 1 0 14 0a7 7 0 1 0 -14 0M21 21l-6 -6',
  users: 'M9 7m-4 0a4 4 0 1 0 8 0a4 4 0 1 0 -8 0M3 21v-2a4 4 0 0 1 4 -4h4a4 4 0 0 1 4 4v2M16 3.13a4 4 0 0 1 0 7.75M21 21v-2a4 4 0 0 0 -3 -3.85',
  mail: 'M3 7a2 2 0 0 1 2 -2h14a2 2 0 0 1 2 2v10a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2zM3 7l9 6l9 -6',
  bookmark: 'M18 7v14l-6 -4l-6 4v-14a4 4 0 0 1 4 -4h4a4 4 0 0 1 4 4z',
  trash: 'M4 7h16M10 11v6M14 11v6M5 7l1 12a2 2 0 0 0 2 2h8a2 2 0 0 0 2 -2l1 -12M9 7v-3a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v3',
  camera: 'M5 7h1a2 2 0 0 0 2 -2a1 1 0 0 1 1 -1h6a1 1 0 0 1 1 1a2 2 0 0 0 2 2h1a2 2 0 0 1 2 2v9a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2v-9a2 2 0 0 1 2 -2M12 13m-3 0a3 3 0 1 0 6 0a3 3 0 1 0 -6 0',
  key: 'M16.555 3.843l3.602 3.602a2.877 2.877 0 0 1 0 4.069l-2.643 2.643a2.877 2.877 0 0 1 -4.069 0l-.301 -.301l-6.558 6.558a2 2 0 0 1 -1.239 .578l-.175 .008h-1.172a1 1 0 0 1 -.993 -.883l-.007 -.117v-1.172a2 2 0 0 1 .467 -1.284l.119 -.13l.414 -.414h2v-2h2v-2l2.144 -2.144l-.301 -.301a2.877 2.877 0 0 1 0 -4.069l2.643 -2.643a2.877 2.877 0 0 1 4.069 0zM15 9h.01',
  up: 'M12 5l0 14M18 11l-6 -6M6 11l6 -6',
  down: 'M12 5l0 14M18 13l-6 6M6 13l6 6',
  circle: 'M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0 -18 0',
  square: 'M3 3m0 2a2 2 0 0 1 2 -2h14a2 2 0 0 1 2 2v14a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2z',
  edit: 'M4 20h4l10.5 -10.5a2.828 2.828 0 1 0 -4 -4l-10.5 10.5v4M13.5 6.5l4 4',
}

export default function Icon({ name, size = 20, className = '', title }) {
  const d = PATHS[name] || PATHS.sparkle
  return (
    <svg
      className={`icon ${className}`} width={size} height={size} viewBox="0 0 24 24"
      fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden={title ? undefined : 'true'} role={title ? 'img' : undefined}
    >
      {title && <title>{title}</title>}
      <path d={d} />
    </svg>
  )
}
