import { NavLink, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext.jsx';

const links = [
  { to: '/',        label: 'DASHBOARD' },
  { to: '/submit',  label: 'SUBMIT' },
  { to: '/results', label: 'RESULTS' },
];

export default function Nav() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  // Derive initials for avatar (up to 2 chars from email local-part)
  const initials = user?.email
    ? user.email.split('@')[0].slice(0, 2).toUpperCase()
    : '??';

  return (
    <nav style={styles.nav}>
      {/* Logo */}
      <span style={styles.logo}>
        llm<span style={styles.logoDot}>·</span>eval
      </span>

      {/* Links */}
      <div style={styles.links}>
        {links.map(({ to, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            style={({ isActive }) => ({
              ...styles.link,
              ...(isActive ? styles.linkActive : {}),
            })}
          >
            {label}
          </NavLink>
        ))}
      </div>

      {/* User + logout */}
      <div style={styles.right}>
        <button onClick={handleLogout} style={styles.logoutBtn} title="Log out">
          LOGOUT
        </button>
        <div style={styles.avatar} title={user?.email ?? ''}>
          {initials}
        </div>
      </div>
    </nav>
  );
}

const styles = {
  nav: {
    background: '#1A1A18',
    padding: '0 24px',
    height: 48,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 24,
    flexShrink: 0,
  },
  logo: {
    fontFamily: "'IBM Plex Mono', monospace",
    fontSize: 12,
    fontWeight: 500,
    color: '#EDEBE6',
    letterSpacing: '0.06em',
  },
  logoDot: {
    color: '#7F77DD',
  },
  links: {
    display: 'flex',
    gap: 24,
    alignItems: 'center',
  },
  link: {
    fontFamily: "'IBM Plex Mono', monospace",
    fontSize: 10,
    letterSpacing: '0.08em',
    color: '#6B6960',
    transition: 'color 0.15s',
  },
  linkActive: {
    color: '#EDEBE6',
  },
  right: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
  },
  logoutBtn: {
    fontFamily: "'IBM Plex Mono', monospace",
    fontSize: 9,
    letterSpacing: '0.08em',
    color: '#6B6960',
    background: 'transparent',
    border: '1px solid #2E2E2C',
    borderRadius: 4,
    padding: '3px 8px',
    cursor: 'pointer',
    transition: 'color 0.15s, border-color 0.15s',
  },
  avatar: {
    width: 26,
    height: 26,
    borderRadius: '50%',
    background: '#3C3489',
    color: '#CECBF6',
    fontFamily: "'IBM Plex Mono', monospace",
    fontSize: 9,
    fontWeight: 500,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  },
};
