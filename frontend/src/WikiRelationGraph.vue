<script setup lang="ts">
import type { Core, ElementDefinition, EventObject } from 'cytoscape'
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

type GraphNode = { id: string; label: string; degree: number }
type GraphEdge = { id: string; source: string; target: string; count: number }

const props = defineProps<{
  nodes: GraphNode[]
  edges: GraphEdge[]
  selectedNode?: string
  selectedEdge?: string
}>()
const emit = defineEmits<{
  selectNode: [id: string]
  selectEdge: [id: string]
  clear: []
}>()

const container = ref<HTMLDivElement | null>(null)
let graph: Core | null = null
const loadCytoscape = () => import('cytoscape')
let cytoscapeModule: ReturnType<typeof loadCytoscape> | null = null

function elements(): ElementDefinition[] {
  return [
    ...props.nodes.map(node => ({
      group: 'nodes' as const,
      data: {
        id: node.id,
        label: node.label.length > 10 ? `${node.label.slice(0, 10)}…` : node.label,
        fullLabel: node.label,
        degree: node.degree,
        size: Math.min(58, 31 + Math.sqrt(Math.max(1, node.degree)) * 3.1),
      },
    })),
    ...props.edges.map(edge => ({
      group: 'edges' as const,
      data: {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        count: edge.count,
        weight: Math.min(7, 1.1 + Math.sqrt(Math.max(1, edge.count))),
      },
    })),
  ]
}

function applySelection() {
  if (!graph) return
  graph.batch(() => {
    graph!.elements().removeClass('is-active is-neighbour is-muted')
    if (props.selectedNode) {
      const node = graph!.$id(props.selectedNode)
      if (node.nonempty()) {
        const neighbourhood = node.closedNeighborhood()
        graph!.elements().not(neighbourhood).addClass('is-muted')
        neighbourhood.addClass('is-neighbour')
        node.addClass('is-active')
      }
    } else if (props.selectedEdge) {
      const edge = graph!.$id(props.selectedEdge)
      if (edge.nonempty()) {
        const active = edge.add(edge.connectedNodes())
        graph!.elements().not(active).addClass('is-muted')
        active.addClass('is-neighbour')
        edge.addClass('is-active')
      }
    }
  })
}

async function renderGraph() {
  await nextTick()
  if (!container.value) return
  const module = await (cytoscapeModule ||= loadCytoscape())
  const cytoscape = module.default
  if (!container.value) return
  graph?.destroy()
  const cy = cytoscape({
    container: container.value,
    elements: elements(),
    minZoom: 0.18,
    maxZoom: 3.2,
    wheelSensitivity: 0.22,
    boxSelectionEnabled: false,
    autoungrabify: false,
    style: [
      { selector: 'node', style: {
        width: 'data(size)', height: 'data(size)', label: 'data(label)',
        'background-color': '#fff', 'border-color': '#739b86', 'border-width': 2,
        color: '#294f42', 'font-family': 'Microsoft YaHei, sans-serif', 'font-size': 10,
        'font-weight': 'bold', 'text-wrap': 'ellipsis', 'text-max-width': '78px',
        'text-valign': 'center', 'text-halign': 'center',
        'overlay-opacity': 0,
      } },
      { selector: 'edge', style: {
        width: 'data(weight)', 'line-color': '#a8bdb1', 'curve-style': 'bezier',
        opacity: 0.72, 'overlay-opacity': 0,
      } },
      { selector: 'node:selected', style: {
        'background-color': '#e2efe7', 'border-color': '#2f6d56', 'border-width': 4,
      } },
      { selector: 'edge:selected', style: { 'line-color': '#30725a', opacity: 1 } },
      { selector: '.is-neighbour', style: { opacity: 1 } },
      { selector: 'node.is-active', style: {
        'background-color': '#315f50', 'border-color': '#214b3e', 'border-width': 4,
        color: '#fff', 'z-index': 10,
      } },
      { selector: 'edge.is-active', style: { 'line-color': '#1f6a50', opacity: 1, 'z-index': 9 } },
      { selector: '.is-muted', style: { opacity: 0.1 } },
    ],
  })
  graph = cy
  cy.on('tap', 'node', (event: EventObject) => emit('selectNode', event.target.id()))
  cy.on('tap', 'edge', (event: EventObject) => emit('selectEdge', event.target.id()))
  cy.on('tap', event => { if (event.target === cy) emit('clear') })
  cy.layout({
    name: 'cose', animate: false, fit: true, padding: 52, randomize: true,
    nodeRepulsion: () => props.nodes.length > 120 ? 10500 : 8200,
    idealEdgeLength: () => props.nodes.length > 120 ? 92 : 78,
    edgeElasticity: () => 110, nestingFactor: 1.2, gravity: 0.9,
    numIter: props.nodes.length > 180 ? 700 : 500,
  }).run()
  applySelection()
}

function fit() { graph?.fit(undefined, 46) }
function changeZoom(factor: number) {
  if (!graph || !container.value) return
  const level = Math.max(graph.minZoom(), Math.min(graph.maxZoom(), graph.zoom() * factor))
  graph.zoom({ level, renderedPosition: { x: container.value.clientWidth / 2, y: container.value.clientHeight / 2 } })
}
function zoomIn() { changeZoom(1.22) }
function zoomOut() { changeZoom(1 / 1.22) }

watch([() => props.nodes, () => props.edges], () => void renderGraph())
watch([() => props.selectedNode, () => props.selectedEdge], applySelection)
onMounted(() => void renderGraph())
onBeforeUnmount(() => { graph?.destroy(); graph = null })
defineExpose({ fit, zoomIn, zoomOut })
</script>

<template>
  <div
    ref="container"
    class="wiki-cytoscape"
    role="img"
    tabindex="0"
    :aria-label="`小说人物关系图，共 ${nodes.length} 个人物、${edges.length} 条关系线`"
  />
</template>
