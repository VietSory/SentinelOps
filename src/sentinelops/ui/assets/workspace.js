import {ApiFailure, IncidentClient, memoryRecoveryReview} from './client.js';

// v2 proposals have no deadline (null); v1 keeps its countdown in seconds.
export const proposalRemaining = (row, now = Date.now()) => row.proposal.proposal_version === 'runbook-proposal/v2' ? null :
  Math.max(0,Math.ceil((Date.parse(row.proposal.expires_at)-now)/1000));
export const proposalOpen = (row, now = Date.now()) => row.proposal.proposal_version === 'runbook-proposal/v2' || proposalRemaining(row, now) > 0;

const blank = () => ({session:false, canApprove:true, filters:{state:'',service:''}, rows:[], nextCursor:null, listLoading:false,
  listError:null, detail:null, detailLoading:false, detailError:null, historyLoading:false, historyError:null,
  readiness:null, readinessError:null, selected:null, selectedRevision:null, updatedAt:null, notice:null,
  actions:null, actionsLoading:false, actionsError:null, quarantines:{},
  explanation:null, explanationLoading:false, explanationError:null,
  proposals:null, proposalsLoading:false, proposalsError:null, decisionLoading:false});

/** Generation guards also reject responses from transports that ignore AbortSignal. */
export class Workspace {
  #client = null; #tasks = new Map(); #factory; #notify;
  constructor(notify = () => {}, factory = token => new IncidentClient(token)) {
    this.state = blank(); this.#factory = factory; this.#notify = notify;
  }
  emit() { this.#notify(this.state); }
  #cancel(key) { this.#tasks.get(key)?.abort(); this.#tasks.delete(key); }
  #clearQuarantines() {
    for (const key of [...this.#tasks.keys()]) if (key.startsWith('quarantine:')) this.#cancel(key);
    this.state.quarantines={};
  }
  async #run(key, action, success, failure, end) {
    this.#cancel(key);
    const controller = new AbortController(); this.#tasks.set(key,controller);
    const current = () => this.#tasks.get(key) === controller;
    try { const value = await action(controller.signal); if (current()) success(value); }
    catch (error) {
      if (!current() || error.name === 'AbortError') return;
      if (error.status === 401 || (error.status === 403 && error.code === 'insufficient_scope')) {
        this.disconnect(error.message); return;
      }
      failure(error instanceof ApiFailure ? error : new ApiFailure(0,'invalid_response'));
    } finally { if (current()) { this.#tasks.delete(key); end(); this.emit(); } }
  }
  async connect(token, {canApprove = true} = {}) {
    this.disconnect(); this.#client = this.#factory(token); this.state.session = true; this.state.canApprove = canApprove; this.emit();
    await Promise.all([this.loadList(),this.checkReadiness()]);
  }
  disconnect(notice = null) {
    for (const key of [...this.#tasks.keys()]) this.#cancel(key);
    this.#client = null; this.state = blank(); this.state.notice = notice; this.emit();
  }
  async checkReadiness() {
    if (!this.#client) return;
    const client = this.#client;
    return this.#run('readiness',s => client.readiness(s),value => {
      this.state.readiness = value; this.state.readinessError = null;
    },error => { this.state.readiness = null; this.state.readinessError = error; },() => {});
  }
  async loadList(append = false) {
    if (!this.#client || (append && !this.state.nextCursor)) return;
    const client = this.#client, filters = {...this.state.filters}, cursor = append ? this.state.nextCursor : null;
    this.state.listLoading = true; this.state.listError = null; this.emit();
    return this.#run('list',s => client.list(filters,cursor,s),value => {
      const rows = append ? [...this.state.rows,...value.items] : value.items;
      this.state.rows = [...new Map(rows.map(row => [row.incident_id,row])).values()];
      this.state.nextCursor = value.next_cursor; this.state.updatedAt = new Date().toISOString();
    },error => { this.state.listError = error; },() => { this.state.listLoading = false; });
  }
  async filter(filters) {
    this.#cancel('proposals'); this.#cancel('decision');
    this.#cancel('detail'); this.#cancel('history'); this.#cancel('actions'); this.#cancel('explanation');
    this.#clearQuarantines();
    Object.assign(this.state,{filters:{...filters},rows:[],nextCursor:null,detail:null,selected:null,selectedRevision:null,
      detailError:null,detailLoading:false,historyLoading:false,historyError:null,actions:null,actionsLoading:false,actionsError:null,
      explanation:null,explanationLoading:false,explanationError:null,
      proposals:null,proposalsLoading:false,proposalsError:null,decisionLoading:false});
    return this.loadList();
  }
  async select(id, revision = null) {
    if (!this.#client) return;
    if (revision !== null && (!Number.isSafeInteger(revision) || revision < 1)) {
      this.state.detailError = new ApiFailure(0,'invalid_response'); this.emit(); return;
    }
    this.#cancel('history'); this.#cancel('actions'); this.#cancel('explanation');
    this.#cancel('proposals'); this.#cancel('decision');
    this.#clearQuarantines();
    const client = this.#client;
    Object.assign(this.state,{selected:id,selectedRevision:revision,detail:null,detailLoading:true,detailError:null,historyError:null,
      historyLoading:false,actions:null,actionsLoading:false,actionsError:null,
      explanation:null,explanationLoading:false,explanationError:null,
      proposals:null,proposalsLoading:false,proposalsError:null,decisionLoading:false});
    this.emit();
    return this.#run('detail',s => client.context(id,revision,0,s),value => { this.state.detail = value; },
      error => { this.state.detailError = error; },() => { this.state.detailLoading = false; });
  }
  async loadProposals() {
    const detail = this.state.detail, client = this.#client;
    if (!client || !detail || this.state.proposalsLoading || this.state.decisionLoading) return;
    Object.assign(this.state,{proposals:null,proposalsLoading:true,proposalsError:null}); this.emit();
    return this.#run('proposals',s => client.proposals(detail.incident_id,detail.revision,s),
      value => { this.state.proposals = value; },error => { this.state.proposalsError = error; },
      () => { this.state.proposalsLoading = false; });
  }
  async decideProposal(id, decision, reason) {
    const row = this.state.proposals?.find(item => item.proposal.proposal_id === id), client = this.#client;
    const manual = row && decision === 'approve' && row.manual_recovery_available === true && Boolean(memoryRecoveryReview(row));
    if (!client || !row || !this.state.canApprove ||
        (!manual && (row.status !== 'pending' || !proposalOpen(row))) || this.state.decisionLoading) return;
    this.state.decisionLoading = true; this.state.proposalsError = null; this.emit();
    return this.#run('decision',s => client.decideProposal(row,decision,reason,s),value => {
      this.state.proposals = value.proposal.proposal_id === row.proposal.proposal_id
        ? this.state.proposals.map(item => item === row ? {...item,...value} : item)
        : [value,...this.state.proposals.map(item => item === row ? {...item,manual_recovery_available:false} : item)
          .filter(item => item.proposal.proposal_id !== value.proposal.proposal_id)];
    },error => {
      // A lost response may have persisted a receipt: require a read before any retry.
      this.state.proposals = null; this.state.proposalsError = error;
    },() => { this.state.decisionLoading = false; });
  }
  async loadHistory() {
    const detail = this.state.detail;
    if (!this.#client || !detail || detail.timeline.next_after_revision === null || this.state.historyLoading) return;
    const client = this.#client;
    this.state.historyLoading = true; this.state.historyError = null; this.emit();
    return this.#run('history',s => client.context(detail.incident_id,detail.revision,detail.timeline.next_after_revision,s),value => {
      this.state.detail = {...detail,timeline:{events:[...new Map([...detail.timeline.events,...value.timeline.events]
        .map(event => [event.event_id,event])).values()],next_after_revision:value.timeline.next_after_revision}};
    },error => { this.state.historyError = error; },() => { this.state.historyLoading = false; });
  }
  async loadExplanation() {
    const detail = this.state.detail;
    if (!this.#client || !detail || this.state.explanationLoading) return;
    const client = this.#client;
    // Remove the previous result during reload, including when the new request fails.
    Object.assign(this.state,{explanation:null,explanationLoading:true,explanationError:null}); this.emit();
    return this.#run('explanation',s => client.explanation(detail.incident_id,detail.revision,
      detail.payload.evidence.map(item => item.id),s),value => { this.state.explanation = value; },
      error => { this.state.explanationError = error; },() => { this.state.explanationLoading = false; });
  }
  async loadActions(append = false) {
    const detail = this.state.detail;
    if (!this.#client || !detail || this.state.actionsLoading || append && !this.state.actions?.next_cursor) return;
    const client = this.#client, cursor = append ? this.state.actions.next_cursor : null;
    if (!append) this.#clearQuarantines();
    this.state.actionsLoading = true; this.state.actionsError = null; this.emit();
    return this.#run('actions',s => client.actions(detail.incident_id,detail.revision,cursor,s),value => {
      const items = append ? [...this.state.actions.items,...value.items] : value.items;
      this.state.actions = {...value,items:[...new Map(items.map(item => [item.action_id,item])).values()]};
    },error => { this.state.actionsError = error; },() => { this.state.actionsLoading = false; });
  }
  async loadQuarantine(id) {
    const item=this.state.actions?.items.find(value => value.action_id===id);
    if (!this.#client || !item || this.state.quarantines[id]?.loading) return;
    const client=this.#client;
    const set=value => { this.state.quarantines={...this.state.quarantines,[id]:value}; };
    set({loading:true,result:null,error:null});this.emit();
    return this.#run(`quarantine:${id}`,s => client.quarantine(id,item.action_revision,s),
      value => {set({loading:true,result:value,error:null});},
      error => {set({loading:true,result:null,error});},
      () => {set({...this.state.quarantines[id],loading:false});});
  }
}
