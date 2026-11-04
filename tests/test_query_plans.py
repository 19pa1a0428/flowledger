import unittest

from scripts.measure_query_plans import summarize_plan


class QueryPlanTests(unittest.TestCase):
    def test_plan_summary_flattens_nodes_and_buffers(self):
        document = {
            "Plan": {
                "Node Type": "Limit", "Total Cost": 12.5,
                "Shared Hit Blocks": 2, "Shared Read Blocks": 1,
                "Plans": [{
                    "Node Type": "Index Scan", "Shared Hit Blocks": 3,
                    "Shared Read Blocks": 4,
                }],
            },
            "Planning Time": 0.2,
            "Execution Time": 0.4,
        }
        self.assertEqual(summarize_plan(document), {
            "node_types": ["Limit", "Index Scan"],
            "total_cost": 12.5,
            "planning_time_ms": 0.2,
            "execution_time_ms": 0.4,
            "shared_hit_blocks": 5,
            "shared_read_blocks": 5,
        })
