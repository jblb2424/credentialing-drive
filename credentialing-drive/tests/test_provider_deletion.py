import unittest
from types import SimpleNamespace

from app.providers import collect_document_tree, delete_document_references


class FakeCollection:
    def __init__(self, children):
        self.children = children

    def stream(self):
        return [SimpleNamespace(reference=child) for child in self.children]


class FakeReference:
    def __init__(self, name, children=()):
        self.name = name
        self._children = children

    def collections(self):
        return [FakeCollection(self._children)] if self._children else []


class FakeBatch:
    def __init__(self, commits):
        self.references = []
        self.commits = commits

    def delete(self, reference):
        self.references.append(reference)

    def commit(self):
        self.commits.append([reference.name for reference in self.references])


class FakeFirestoreClient:
    def __init__(self):
        self.commits = []

    def batch(self):
        return FakeBatch(self.commits)


class ProviderDeletionTests(unittest.TestCase):
    def test_collect_document_tree_orders_children_before_their_parent(self):
        grandchild = FakeReference("grandchild")
        child = FakeReference("child", [grandchild])
        root = FakeReference("root", [child])

        references = collect_document_tree(root)

        self.assertEqual([reference.name for reference in references], ["grandchild", "child", "root"])

    def test_delete_document_references_commits_in_batches(self):
        client = FakeFirestoreClient()
        references = [FakeReference(str(index)) for index in range(5)]

        delete_document_references(client, references, batch_size=2)

        self.assertEqual(client.commits, [["0", "1"], ["2", "3"], ["4"]])


if __name__ == "__main__":
    unittest.main()
