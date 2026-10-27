import { createContext, useContext } from "react";

export const WorkspaceContext = createContext(null);

export function useWorkspace() {
  return useContext(WorkspaceContext);
}
