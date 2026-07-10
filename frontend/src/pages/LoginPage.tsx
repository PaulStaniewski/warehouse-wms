import { FormEvent, useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import axios from "axios";

import { useAuth } from "../api/AuthContext";

function getErrorMessage(error: unknown) {
  return axios.isAxiosError(error) ? error.response?.data?.detail || "Login failed." : "Login failed.";
}

export function LoginPage() {
  const auth = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    localStorage.removeItem("warehouse-wms-active-branch");
  }, []);

  if (auth.isAuthenticated) {
    return <Navigate to="/wms/dashboard" replace />;
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setIsSubmitting(true);
    try {
      await auth.login(username, password);
    } catch (loginError) {
      setError(getErrorMessage(loginError));
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="login-page">
      <form className="login-panel" onSubmit={handleSubmit}>
        <div>
          <span className="login-kicker">Warehouse WMS</span>
          <h1>Sign in</h1>
          <p>Use your demo branch account to access the WMS console.</p>
        </div>

        <label>
          <span>Username</span>
          <input
            autoComplete="username"
            autoFocus
            onChange={(event) => setUsername(event.target.value)}
            value={username}
          />
        </label>

        <label>
          <span>Password</span>
          <input
            autoComplete="current-password"
            onChange={(event) => setPassword(event.target.value)}
            type="password"
            value={password}
          />
        </label>

        {error && <div className="login-error">{error}</div>}

        <button disabled={!username.trim() || !password || isSubmitting} type="submit">
          {isSubmitting ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </main>
  );
}
