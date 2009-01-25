# Copyright (C) 2008, 2009 Michael Trier (mtrier@gmail.com) and contributors
    def __init__(self, repo, a_path, b_path, a_commit, b_commit, a_mode,
                 b_mode, new_file, deleted_file, rename_from,
                 rename_to, diff):
        self.rename_from = rename_from
        self.rename_to = rename_to
        self.renamed = rename_from != rename_to
        diff_header = re.compile(r"""
            #^diff[ ]--git
                [ ]a/(?P<a_path>\S+)[ ]b/(?P<b_path>\S+)\n
            (?:^similarity[ ]index[ ](?P<similarity_index>\d+)%\n
               ^rename[ ]from[ ](?P<rename_from>\S+)\n
               ^rename[ ]to[ ](?P<rename_to>\S+)(?:\n|$))?
            (?:^old[ ]mode[ ](?P<old_mode>\d+)\n
               ^new[ ]mode[ ](?P<new_mode>\d+)(?:\n|$))?
            (?:^new[ ]file[ ]mode[ ](?P<new_file_mode>.+)(?:\n|$))?
            (?:^deleted[ ]file[ ]mode[ ](?P<deleted_file_mode>.+)(?:\n|$))?
            (?:^index[ ](?P<a_commit>[0-9A-Fa-f]+)
                \.\.(?P<b_commit>[0-9A-Fa-f]+)[ ]?(?P<b_mode>.+)?(?:\n|$))?
        """, re.VERBOSE | re.MULTILINE).match
        for diff in ('\n' + text).split('\ndiff --git')[1:]:
            header = diff_header(diff)
            a_path, b_path, similarity_index, rename_from, rename_to, \
                old_mode, new_mode, new_file_mode, deleted_file_mode, \
                a_commit, b_commit, b_mode = header.groups()
            new_file, deleted_file = bool(new_file_mode), bool(deleted_file_mode)
            diffs.append(Diff(repo, a_path, b_path, a_commit, b_commit,
                old_mode or deleted_file_mode, new_mode or new_file_mode or b_mode,
                new_file, deleted_file, rename_from, rename_to, diff[header.end():]))
