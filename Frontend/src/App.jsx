import { BrowserRouter, Route, Routes } from 'react-router-dom'

import Landingpage from './pages/Landingpage.jsx';
import MainApp from './pages/MainApp.jsx';
import AuthPage from './pages/AuthPage.jsx';
import AuthProvider from './AuthProvider.jsx';
import useAuth from './useAuth.js';

function ProtectedWorkspace() {
  const { user, loading } = useAuth();
  if (loading) return <div className="min-h-screen grid place-items-center bg-[#0b0d12] text-gray-300">Securing your workspace...</div>;
  return user ? <MainApp /> : <AuthPage />;
}

function App() {
  return (
    <>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path='/' element={ <Landingpage/> } />
            <Route path='/MainApp' element={ <ProtectedWorkspace/> } />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </>
  )
}

export default App
