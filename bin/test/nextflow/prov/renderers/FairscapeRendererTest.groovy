/*
 * Copyright 2026, FAIRSCAPE (nf-fairscape fork)
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package nextflow.prov.renderers

import spock.lang.Specification

class FairscapeRendererTest extends Specification {

    def 'should mint valid deterministic ARKs' () {
        when:
        def ark1 = FairscapeRenderer.mintArk('59853', 'dataset', 'output file.txt', '/work/ab/12/output file.txt')
        def ark2 = FairscapeRenderer.mintArk('59853', 'dataset', 'output file.txt', '/work/ab/12/output file.txt')
        def ark3 = FairscapeRenderer.mintArk('59853', 'dataset', 'output file.txt', '/work/cd/34/output file.txt')
        then:
        ark1 ==~ /^ark:[0-9]{5}\/[a-zA-Z0-9_\-]+$/
        ark1 == ark2
        ark1 != ark3
        ark1.startsWith('ark:59853/dataset-output-file-txt-')
    }

    def 'should slugify names' () {
        expect:
        FairscapeRenderer.slugify(name) == slug

        where:
        name                    | slug
        'FetchSequences (1)'    | 'fetchsequences-1'
        'Übung/Straße.txt'      | 'bung-stra-e-txt'
        '---'                   | 'unnamed'
        null                    | 'unnamed'
        'a' * 50                | 'a' * 40
    }

    def 'should enforce minimum description length' () {
        expect:
        FairscapeRenderer.ensureDescription(value, 'A generated fallback description') == expected

        where:
        value        | expected
        null         | 'A generated fallback description'
        'too short'  | 'A generated fallback description'
        'this one is long enough' | 'this one is long enough'
    }

}
