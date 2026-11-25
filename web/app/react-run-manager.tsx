import React, { createContext, useContext, useMemo } from 'react';

export interface ManagedReactRun {
  runId: string;
  convUid: string;
  bufferedEvents: unknown[];
  completed?: boolean;
  processPayload?: (payload: any) => void;
  [key: string]: any;
}

class ReactRunManager {
  private readonly runs = new Map<string, ManagedReactRun>();
  private readonly surfaces = new Map<string, unknown>();
  private activeConversation: string | null = null;

  register(run: ManagedReactRun) {
    this.runs.set(run.runId, run);
  }

  get(runId: string) {
    return this.runs.get(runId) || null;
  }

  findByConversation(convUid: string | null) {
    if (!convUid) return null;
    return [...this.runs.values()].find(run => run.convUid === convUid && !run.completed && !run.cancelled) || null;
  }

  hasConversationRun(convUid: string) {
    return [...this.runs.values()].some(
      run => run.convUid === convUid && (!run.completed || run.bufferedEvents.length > 0),
    );
  }

  cacheSurface<T>(convUid: string | null, surface: T) {
    if (convUid) this.surfaces.set(convUid, surface);
  }

  getSurface<T>(convUid: string) {
    return (this.surfaces.get(convUid) as T | undefined) ?? null;
  }

  deleteSurface(convUid: string) {
    this.surfaces.delete(convUid);
  }

  discardCompletedRuns(convUid: string) {
    for (const [runId, run] of this.runs) {
      if (run.convUid === convUid && run.completed) this.runs.delete(runId);
    }
  }

  detachConversation(convUid: string | null) {
    if (!convUid) return;
    if (this.activeConversation === convUid) this.activeConversation = null;
    for (const run of this.runs.values()) {
      if (run.convUid !== convUid || run.completed || run.cancelled) continue;
      run.detached = true;
      run.processPayload = undefined;
    }
  }

  setActiveConversation(convUid: string | null) {
    this.activeConversation = convUid;
  }

  activateConversation(convUid: string | null) {
    this.activeConversation = convUid;
    if (!convUid) return null;

    for (const run of this.runs.values()) {
      if (run.convUid !== convUid) continue;
      if (run.detached || !run.processPayload) continue;
      const events = run.bufferedEvents.splice(0);
      events.forEach(payload => run.processPayload?.(payload));
      if (run.completed) this.runs.delete(run.runId);
    }

    return this.findByConversation(convUid);
  }

  dispatch(runId: string, payload: any) {
    const run = this.runs.get(runId);
    if (!run || run.cancelled || run.completed) return;
    if (!run.detached && this.activeConversation === run.convUid && run.processPayload) {
      run.processPayload(payload);
      return;
    }
    run.bufferedEvents.push(payload);
  }

  complete(runId: string) {
    const run = this.runs.get(runId);
    if (!run) return;
    run.completed = true;
    if (this.activeConversation === run.convUid && !run.detached && run.processPayload) {
      this.runs.delete(runId);
    }
  }

  remove(runId: string) {
    this.runs.delete(runId);
  }

  clear() {
    for (const run of this.runs.values()) {
      run.cancelled = true;
      run.reader?.cancel().catch(() => undefined);
      run.controller?.abort();
    }
    this.runs.clear();
    this.surfaces.clear();
    this.activeConversation = null;
  }
}

const manager = new ReactRunManager();
const ReactRunManagerContext = createContext(manager);

export function ReactRunManagerProvider({ children }: { children: React.ReactNode }) {
  const value = useMemo(() => manager, []);
  return <ReactRunManagerContext.Provider value={value}>{children}</ReactRunManagerContext.Provider>;
}

export function useReactRunManager() {
  return useContext(ReactRunManagerContext);
}
