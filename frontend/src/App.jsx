import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AuthProvider, useAuth } from './context/AuthContext.jsx';
import Nav from './components/Nav.jsx';
import Login from './pages/Login.jsx';
import Register from './pages/Register.jsx';
import Dashboard from './pages/Dashboard.jsx';
import SubmissionForm from './pages/SubmissionForm.jsx';
import ResultsTable from './pages/ResultsTable.jsx';
import ResultDetail from './pages/ResultDetail.jsx';

// ---------------------------------------------------------------------------
// Route guard — redirects unauthenticated users to /login
// ---------------------------------------------------------------------------

function PrivateRoute({ children }) {
  const { isAuthenticated } = useAuth();
  return isAuthenticated ? children : <Navigate to="/login" replace />;
}

// ---------------------------------------------------------------------------
// App shell — wraps authenticated pages in the shared nav layout
// ---------------------------------------------------------------------------

function AppShell({ children }) {
  return (
    <div className="app-layout">
      <Nav />
      <main className="page-content">{children}</main>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          {/* Public */}
          <Route path="/login"    element={<Login />} />
          <Route path="/register" element={<Register />} />

          {/* Protected */}
          <Route
            path="/"
            element={
              <PrivateRoute>
                <AppShell><Dashboard /></AppShell>
              </PrivateRoute>
            }
          />
          <Route
            path="/submit"
            element={
              <PrivateRoute>
                <AppShell><SubmissionForm /></AppShell>
              </PrivateRoute>
            }
          />
          <Route
            path="/results"
            element={
              <PrivateRoute>
                <AppShell><ResultsTable /></AppShell>
              </PrivateRoute>
            }
          />
          <Route
            path="/results/:resultId"
            element={
              <PrivateRoute>
                <AppShell><ResultDetail /></AppShell>
              </PrivateRoute>
            }
          />

          {/* Fallback */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
