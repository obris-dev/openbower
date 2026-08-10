// Shared between the Sidebar (toggle writes) and the head script (pre-paint
// read); its own module so the server-rendered script component does not
// import the client component.
export const SIDEBAR_COLLAPSE_KEY = "bower.sidebar.collapsed";
