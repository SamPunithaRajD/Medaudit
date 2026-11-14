import axios from 'axios';
export const useMocks=import.meta.env.VITE_USE_MOCK_DATA!=='false';
export const api=axios.create({baseURL:import.meta.env.VITE_API_BASE_URL||'http://localhost:8000',timeout:20000});
api.interceptors.response.use(r=>r,err=>Promise.reject({message:err.response?.data?.message||err.message||'Service unavailable',status:err.response?.status||0,code:err.code||'NETWORK_ERROR'}));
