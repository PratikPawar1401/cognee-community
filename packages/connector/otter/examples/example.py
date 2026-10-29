"""Run from packages/connector/otter with credentials in dlt secrets/environment."""

import argparse
import asyncio

import cognee

from cognee_community_connector_otter import otter_source


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--conversation-id", action="append")
    scope.add_argument("--channel-id", action="append")
    parser.add_argument("--include-shared", action="store_true")
    parser.add_argument("--dataset", default="otter-meetings")
    parser.add_argument(
        "--query", default="What decisions were made and who owns the action items?"
    )
    args = parser.parse_args()
    await cognee.remember(
        otter_source(
            conversation_ids=args.conversation_id,
            channel_ids=args.channel_id,
            include_shared=args.include_shared,
        ),
        dataset_name=args.dataset,
        primary_key="id",
        write_disposition="replace",
        max_rows_per_table=0,
        self_improvement=False,
    )
    result = await cognee.search(
        query_text=args.query,
        query_type=cognee.SearchType.GRAPH_COMPLETION,
        datasets=[args.dataset],
    )
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
