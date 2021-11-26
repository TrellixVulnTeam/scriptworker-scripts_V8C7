from scriptworker import artifacts
from scriptworker.utils import get_single_item_from_sequence
from scriptworker_client.exceptions import TaskVerificationError


def get_msix_file_path(context):
    artifacts_per_task_id, _ = artifacts.get_upstream_artifacts_full_paths_per_task_id(context)

    all_artifacts = [artifact for artifacts in artifacts_per_task_id.values() for artifact in artifacts]

    return get_single_item_from_sequence(
        all_artifacts,
        condition=lambda artifact: artifact.endswith(".store.msix"),
        ErrorClass=TaskVerificationError,
        no_item_error_message="No upstream artifact is a store msix",
        too_many_item_error_message="Too many msix artifacts found",
    )
