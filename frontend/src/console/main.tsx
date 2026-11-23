import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { ThemeProvider } from '../ui/theme';
import { OverlayProvider } from '../ui/overlays';
import App from './App';
import './console.css';

createRoot(document.getElementById('root') as HTMLElement).render(
  <StrictMode>
    <ThemeProvider>
      <OverlayProvider>
        <BrowserRouter basename="/console">
          <App />
        </BrowserRouter>
      </OverlayProvider>
    </ThemeProvider>
  </StrictMode>,
);
