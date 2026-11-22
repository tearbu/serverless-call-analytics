#!/usr/bin/env python3
"""Structural check of template.yaml, runnable without AWS.

Parses the SAM template with PyYAML (a loader that tolerates the
!Ref, !Sub, and !GetAtt intrinsic tags) and asserts the shape this
project depends on: the five resources exist with the right types,
the table is on-demand with a NEW_IMAGE stream, each function holds
only the IAM actions its job needs, and the query function is
read-only.

This is a structure check, not a deployment. It does not catch every
error SAM or CloudFormation would, and it needs PyYAML installed
(the handlers and demo do not).

Usage: python3 validate_template.py
"""

import os
import sys

import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))


class SamLoader(yaml.SafeLoader):
    pass


def _unknown_tag(loader, tag_suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return {tag_suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {tag_suffix: loader.construct_sequence(node)}
    return {tag_suffix: loader.construct_mapping(node)}


SamLoader.add_multi_constructor("!", _unknown_tag)


def function_actions(resources, name):
    actions = []
    for policy in resources[name]["Properties"]["Policies"]:
        for statement in policy["Statement"]:
            actions.extend(statement["Action"])
    return sorted(actions)


def main():
    with open(os.path.join(ROOT, "template.yaml"), encoding="utf-8") as handle:
        template = yaml.load(handle, Loader=SamLoader)

    assert template["Transform"] == "AWS::Serverless-2016-10-31"
    resources = template["Resources"]
    expected = {
        "CallsBucket": "AWS::S3::Bucket",
        "CallsTable": "AWS::DynamoDB::Table",
        "IngestFunction": "AWS::Serverless::Function",
        "AnalyzeFunction": "AWS::Serverless::Function",
        "QueryFunction": "AWS::Serverless::Function",
    }
    for name, resource_type in expected.items():
        assert name in resources, f"missing resource {name}"
        assert resources[name]["Type"] == resource_type, f"{name} has wrong type"
        print(f"resource ok: {name} ({resource_type})")

    table = resources["CallsTable"]["Properties"]
    assert table["BillingMode"] == "PAY_PER_REQUEST"
    assert table["StreamSpecification"]["StreamViewType"] == "NEW_IMAGE"
    print("table ok: on-demand billing, NEW_IMAGE stream")

    query_actions = function_actions(resources, "QueryFunction")
    assert query_actions == ["dynamodb:GetItem", "dynamodb:Scan"], (
        f"query function must stay read-only, found {query_actions}"
    )
    for name in ("IngestFunction", "AnalyzeFunction", "QueryFunction"):
        actions = function_actions(resources, name)
        assert not any("Delete" in action for action in actions), name
        print(f"iam ok: {name}: {actions}")

    assert "ApiUrl" in template["Outputs"]
    print("TEMPLATE VALIDATION PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
