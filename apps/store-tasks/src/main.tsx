import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App, takeSignIn } from "./App";
import { ProjectScreen } from "./Tracker";
import "./index.css";
import { recordErrors } from "./steps";

recordErrors();

if ("serviceWorker" in navigator && import.meta.env.PROD) {
  navigator.serviceWorker.register("/sw.js");
}

// Read before routing: coming back from Google sign-in may move a project member to their project's page.
const signInError = takeSignIn();
const project = /^\/p\/([\w-]+)\/?$/.exec(location.pathname)?.[1];

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {project ? <ProjectScreen id={project} signInError={signInError} /> : <App signInError={signInError} />}
  </StrictMode>,
);
