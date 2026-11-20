import * as React from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
  MarkerType,
  type Node,
  type Edge,
  type NodeProps,
} from '@xyflow/react';
import { cn } from '@/lib/utils';
import { useLocale } from '@/i18n/LocaleContext';
import type { Incident, ServiceHealthOverview } from '@/types/sentinelops';

interface ServiceNodeData extends Record<string, unknown> {
  label: string;
  role: string;
  latencyText: string;
  errorText: string;
  health: 'healthy' | 'warning' | 'critical';
  highlightTag?: string;
}

function ServiceNode({ data }: NodeProps<Node<ServiceNodeData>>) {
  const styles = {
    healthy: {
      ring: 'border-emerald-300 bg-white',
      dot: 'bg-emerald-500',
      badge: 'bg-emerald-50 text-emerald-700',
    },
    warning: {
      ring: 'border-orange-400 bg-orange-50/40 ring-2 ring-orange-100',
      dot: 'bg-orange-500',
      badge: 'bg-orange-100 text-orange-800',
    },
    critical: {
      ring: 'border-red-400 bg-red-50/50 ring-2 ring-red-100',
      dot: 'bg-red-600 status-pulse text-red-500',
      badge: 'bg-red-100 text-red-700',
    },
  }[data.health];

  return (
    <div
      className={cn(
        'min-w-[178px] rounded-xl border px-3.5 py-2.5 shadow-sm transition-shadow hover:shadow-md',
        styles.ring,
      )}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!h-2 !w-2 !border-white !bg-slate-400"
      />
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className={cn('h-2 w-2 rounded-full shrink-0', styles.dot)} />
          <span className="text-sm font-bold text-slate-900">{data.label}</span>
        </div>
        <span className="text-[10px] font-medium uppercase tracking-wide text-slate-400">
          {data.role}
        </span>
      </div>
      <div className="mt-2 flex items-center justify-between gap-2 text-xs tabular-nums">
        <span className="text-slate-600">
          p95 <strong className="text-slate-900">{data.latencyText}</strong>
        </span>
        <span className={cn('rounded px-1.5 py-0.5 text-[11px] font-semibold', styles.badge)}>
          {data.errorText}
        </span>
      </div>
      {data.highlightTag && (
        <div className="mt-2 rounded-md bg-orange-600 px-2 py-0.5 text-[10px] font-semibold text-white">
          {data.highlightTag}
        </div>
      )}
      <Handle
        type="source"
        position={Position.Right}
        className="!h-2 !w-2 !border-white !bg-slate-400"
      />
    </div>
  );
}

const nodeTypes = { serviceNode: ServiceNode };

export interface ServiceDependencyMapProps {
  incident: Incident;
  services: ServiceHealthOverview[];
  className?: string;
}

export function ServiceDependencyMap({
  incident,
  services,
  className,
}: ServiceDependencyMapProps) {
  const { isEn } = useLocale();
  const isPaymentIncident = incident.primaryService === 'Payment' && incident.status !== 'recovered';
  const isInventoryIncident = incident.primaryService === 'Inventory' && incident.status !== 'recovered';

  const checkoutSvc = services.find((s) => s.name === 'Checkout');
  const paymentSvc = services.find((s) => s.name === 'Payment');
  const inventorySvc = services.find((s) => s.name === 'Inventory');

  const nodes = React.useMemo<Node<ServiceNodeData>[]>(() => {
    const checkoutHealth: ServiceNodeData['health'] =
      isPaymentIncident || isInventoryIncident ? 'warning' : 'healthy';
    const paymentHealth: ServiceNodeData['health'] = isPaymentIncident
      ? incident.severity === 'critical'
        ? 'critical'
        : 'warning'
      : 'healthy';
    const paymentDbHealth: ServiceNodeData['health'] = isPaymentIncident ? 'critical' : 'healthy';
    const inventoryHealth: ServiceNodeData['health'] = isInventoryIncident ? 'warning' : 'healthy';
    const inventoryDbHealth: ServiceNodeData['health'] = isInventoryIncident ? 'critical' : 'healthy';

    return [
      {
        id: 'checkout',
        type: 'serviceNode',
        position: { x: 20, y: 115 },
        data: {
          label: 'Checkout',
          role: 'Gateway',
          latencyText: `${checkoutSvc?.p95LatencyMs ?? incident.metricsSnapshot.p95LatencyMs} ms`,
          errorText: isPaymentIncident
            ? isEn ? '502 · 14.2%' : '502 · 14,2%'
            : `${checkoutSvc?.errorRatePct ?? 0}% err`,
          health: checkoutHealth,
          highlightTag: isPaymentIncident
            ? isEn ? 'Cascading 502 Errors' : 'Lan truyền lỗi 502'
            : undefined,
        },
      },
      {
        id: 'payment',
        type: 'serviceNode',
        position: { x: 280, y: 24 },
        data: {
          label: 'Payment',
          role: 'Service',
          latencyText: `${paymentSvc?.p95LatencyMs ?? 1240} ms`,
          errorText: isPaymentIncident
            ? isEn ? '503 · 18.4%' : '503 · 18,4%'
            : isEn ? '0.1% err' : '0,1% err',
          health: paymentHealth,
          highlightTag: isPaymentIncident
            ? isEn ? 'Suspected Root Cause' : 'Root Cause nghi ngờ'
            : undefined,
        },
      },
      {
        id: 'payment-db',
        type: 'serviceNode',
        position: { x: 545, y: 24 },
        data: {
          label: 'Payment DB',
          role: 'PostgreSQL',
          latencyText: isPaymentIncident ? '870 ms' : '24 ms',
          errorText: isPaymentIncident ? 'Pool 5/5' : 'Pool 4/25',
          health: paymentDbHealth,
          highlightTag: isPaymentIncident ? 'Bottleneck · Timeout' : undefined,
        },
      },
      {
        id: 'inventory',
        type: 'serviceNode',
        position: { x: 280, y: 208 },
        data: {
          label: 'Inventory',
          role: 'Service',
          latencyText: isInventoryIncident ? '890 ms' : `${inventorySvc?.p95LatencyMs ?? 48} ms`,
          errorText: isInventoryIncident
            ? isEn ? '0.8% err' : '0,8% err'
            : isEn ? '0.0% err' : '0,0% err',
          health: inventoryHealth,
          highlightTag: isInventoryIncident
            ? isEn ? 'Suspected Root Cause' : 'Root Cause nghi ngờ'
            : undefined,
        },
      },
      {
        id: 'inventory-db',
        type: 'serviceNode',
        position: { x: 545, y: 208 },
        data: {
          label: 'Inventory DB',
          role: 'PostgreSQL',
          latencyText: isInventoryIncident ? '792 ms' : '38 ms',
          errorText: isInventoryIncident ? 'Row Lock' : 'OK',
          health: inventoryDbHealth,
          highlightTag: isInventoryIncident ? 'Bottleneck · Lock' : undefined,
        },
      },
    ];
  }, [checkoutSvc, incident, inventorySvc, isEn, isInventoryIncident, isPaymentIncident, paymentSvc]);

  const edges = React.useMemo<Edge[]>(() => {
    const paymentEdgeBad = isPaymentIncident;
    const inventoryEdgeBad = isInventoryIncident;

    return [
      {
        id: 'e-checkout-payment',
        source: 'checkout',
        target: 'payment',
        label: paymentEdgeBad ? '503 · 980 ms' : '200 · 86 ms',
        animated: paymentEdgeBad,
        style: {
          stroke: paymentEdgeBad ? '#DC2626' : '#94A3B8',
          strokeWidth: paymentEdgeBad ? 2.6 : 1.6,
        },
        labelStyle: {
          fill: paymentEdgeBad ? '#B91C1C' : '#475569',
          fontWeight: 600,
          fontSize: 11,
        },
        labelBgStyle: {
          fill: paymentEdgeBad ? '#FEF2F2' : '#FFFFFF',
          fillOpacity: 0.95,
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: paymentEdgeBad ? '#DC2626' : '#94A3B8',
        },
      },
      {
        id: 'e-payment-db',
        source: 'payment',
        target: 'payment-db',
        label: paymentEdgeBad ? 'Timeout · 870 ms' : 'OK · 24 ms',
        animated: paymentEdgeBad,
        style: {
          stroke: paymentEdgeBad ? '#EA580C' : '#94A3B8',
          strokeWidth: paymentEdgeBad ? 2.8 : 1.6,
        },
        labelStyle: {
          fill: paymentEdgeBad ? '#C2410C' : '#475569',
          fontWeight: 700,
          fontSize: 11,
        },
        labelBgStyle: {
          fill: paymentEdgeBad ? '#FFF7ED' : '#FFFFFF',
          fillOpacity: 0.95,
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: paymentEdgeBad ? '#EA580C' : '#94A3B8',
        },
      },
      {
        id: 'e-checkout-inventory',
        source: 'checkout',
        target: 'inventory',
        label: inventoryEdgeBad ? (isEn ? 'Slow · 890 ms' : 'Chậm · 890 ms') : '200 · 96 ms',
        animated: inventoryEdgeBad,
        style: {
          stroke: inventoryEdgeBad ? '#EA580C' : '#10B981',
          strokeWidth: inventoryEdgeBad ? 2.4 : 1.6,
        },
        labelStyle: {
          fill: inventoryEdgeBad ? '#C2410C' : '#047857',
          fontWeight: 600,
          fontSize: 11,
        },
        labelBgStyle: {
          fill: inventoryEdgeBad ? '#FFF7ED' : '#ECFDF5',
          fillOpacity: 0.95,
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: inventoryEdgeBad ? '#EA580C' : '#10B981',
        },
      },
      {
        id: 'e-inventory-db',
        source: 'inventory',
        target: 'inventory-db',
        label: inventoryEdgeBad ? 'Lock · 792 ms' : 'OK · 38 ms',
        animated: inventoryEdgeBad,
        style: {
          stroke: inventoryEdgeBad ? '#DC2626' : '#10B981',
          strokeWidth: inventoryEdgeBad ? 2.8 : 1.6,
        },
        labelStyle: {
          fill: inventoryEdgeBad ? '#B91C1C' : '#047857',
          fontWeight: 700,
          fontSize: 11,
        },
        labelBgStyle: {
          fill: inventoryEdgeBad ? '#FEF2F2' : '#ECFDF5',
          fillOpacity: 0.95,
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: inventoryEdgeBad ? '#DC2626' : '#10B981',
        },
      },
    ];
  }, [isEn, isInventoryIncident, isPaymentIncident]);

  return (
    <div
      className={cn(
        'h-[330px] w-full rounded-xl border border-slate-200 bg-[#FCFDFE] overflow-hidden',
        className,
      )}
      aria-label={
        isEn
          ? 'Service dependency map for Checkout, Payment, and Inventory'
          : 'Bản đồ phụ thuộc dịch vụ Checkout, Payment, Inventory'
      }
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        nodesDraggable={false}
        nodesConnectable={false}
        panOnScroll={false}
        zoomOnScroll={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background color="#E2E8F0" gap={20} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
